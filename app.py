from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
import os
import logging
logging.basicConfig(level=logging.DEBUG)
import re

app = Flask(__name__)
uploaded_file = None
current_data = None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    global uploaded_file, current_data
    file = request.files['file']
    if file:
        uploaded_file = file.filename
        file_path = os.path.join('uploads', uploaded_file)
        os.makedirs('uploads', exist_ok=True)
        file.save(file_path)

        if uploaded_file.endswith('.csv'):
            current_data = pd.read_csv(file_path)
        elif uploaded_file.endswith('.xlsx'):
            current_data = pd.read_excel(file_path)
        else:
            return jsonify({"error": "Unsupported file type."}), 400

        # Normalize column names to remove problematic characters
        current_data.columns = (
            current_data.columns
            .str.replace(r'\s+', ' ', regex=True)  # Replace multiple spaces/newlines with single space
            .str.strip()                          # Strip leading/trailing spaces
            .str.replace('"', '')                 # Remove double quotes
        )

        return jsonify({"headers": list(current_data.columns)})
    return jsonify({"error": "No file uploaded."}), 400

@app.route('/update_columns', methods=['POST'])
def update_columns():
    global current_data
    if current_data is None:
        return jsonify({"error": "No data loaded."}), 400

    updates = request.json.get('updates', [])
    columns_to_drop = []
    rename_mapping = {}

    # Normalize current column names
    current_data.columns = (
        current_data.columns
        .str.replace(r'\s+', ' ', regex=True)
        .str.strip()
        .str.replace('"', '')
    )

    for update in updates:
        column_name = update['name'].replace('"', '').strip()  # Sanitize incoming name
        if not update['include']:
            columns_to_drop.append(column_name)
        elif update['rename']:
            # rename_mapping[column_name] = update['rename']
            new_name = update['rename'] if update['rename'] else column_name
            rename_mapping[column_name] = new_name

    # Drop columns
    if columns_to_drop:
        current_data.drop(columns=columns_to_drop, inplace=True, errors='ignore')

    # Rename columns
    if rename_mapping:
        current_data.rename(columns=rename_mapping, inplace=True)
    
    return jsonify({"message": "Columns updated successfully.", "updated_headers": list(current_data.columns)})

@app.route('/get_headers', methods=['GET'])
def get_headers():
    global current_data
    if current_data is None:
        return jsonify({"error": "No data loaded."}), 400

    return jsonify({"headers": list(current_data.columns)})

@app.route('/add_columns', methods=['POST'])
def add_columns():
    global current_data
    if current_data is None:
        return jsonify({"error": "No data loaded."}), 400

    columns = request.json.get('columns', [])
    for column in columns:
        name = column.get('name')
        value = column.get('value')
        if name:
            current_data[name] = value

    return jsonify({"message": "New columns added successfully."})

@app.route('/check_duplicates', methods=['GET'])
def check_duplicates():
    global current_data
    if current_data is None:
        return jsonify({"error": "No data loaded."}), 400

    duplicate_counts = current_data.apply(lambda col: col[col.notna()].duplicated().sum())
    duplicates = {col: count for col, count in duplicate_counts.items() if count > 0}

    if not duplicates:
        return jsonify({"message": "No duplicates found."})

    return jsonify(duplicates)

@app.route('/show_duplicates', methods=['POST'])
def show_duplicates():
    global current_data
    data = request.json
    column = data.get('column')
    sort_column = data.get('sort_column', column)  # Column to sort by
    sort_order = data.get('sort_order', 'asc')  # 'asc' or 'desc'

    if column not in current_data.columns:
        return jsonify({"error": f"Column '{column}' not found in the dataset."}), 400

    filtered_data = current_data[~current_data[column].isna() & (current_data[column] != "N/A")]
    duplicates = filtered_data[filtered_data.duplicated(subset=[column], keep=False)]

    if duplicates.empty:
        return jsonify({"message": "No duplicates found.", "duplicates": []}), 200

    # Apply sorting
    duplicates = duplicates.sort_values(
        by=sort_column,
        ascending=(sort_order == 'asc')
    )

    duplicates = duplicates.fillna("N/A")
    duplicates.reset_index(inplace=True)  # Include index as a proper column
    duplicates_list = duplicates.to_dict(orient='records')

    return jsonify({
        "column": column,
        "duplicates": duplicates_list,
        "total": len(duplicates_list)
    }), 200

@app.route('/process_duplicates', methods=['POST'])
def process_duplicates():
    global current_data
    if current_data is None:
        return jsonify({"error": "No data loaded."}), 400

    action = request.json.get('action')  # "merge" or "delete"
    rows_to_process = request.json.get('rows', [])
    column = request.json.get('column')

    if column not in current_data.columns:
        return jsonify({"error": "Invalid column name."}), 400

    # Reset the index to ensure consistency
    current_data.reset_index(drop=True, inplace=True)

    if action == 'merge':
        # Select rows to merge based on their current index
        try:
            rows_to_merge = current_data.iloc[rows_to_process]
        except IndexError:
            return jsonify({"error": "One or more indices are invalid."}), 400

        # Define a function to merge rows
        def merge_rows(rows):
            merged_row = {}
            for col in rows.columns:
                unique_values = list(filter(pd.notna, set(rows[col].astype(str).dropna())))
                merged_row[col] = "\n".join(unique_values) if unique_values else None
            return merged_row

        # Merge rows and drop old ones
        merged_row = merge_rows(rows_to_merge)
        current_data.drop(index=rows_to_process, inplace=True)
        current_data = pd.concat([current_data, pd.DataFrame([merged_row])], ignore_index=True)
    elif action == 'delete':
        try:
            current_data.drop(index=rows_to_process, inplace=True)
        except IndexError:
            return jsonify({"error": "One or more indices are invalid."}), 400
    else:
        return jsonify({"error": "Invalid action."}), 400

    # Reset the index again after modification
    current_data.reset_index(drop=True, inplace=True)

    return jsonify({"message": f"Duplicates {action}d successfully."})

@app.route('/save', methods=['POST'])
def save_file():
    global current_data
    if current_data is None:
        return jsonify({"error": "No data loaded."}), 400

    save_path = os.path.join('downloads', "updated_file.csv")
    os.makedirs('downloads', exist_ok=True)

    try:
        current_data.to_csv(save_path, index=False)  # Save with all columns, including new ones
        return jsonify({"message": "File saved successfully.", "path": save_path})
    except Exception as e:
        return jsonify({"error": f"Failed to save file: {str(e)}"}), 500


@app.route('/download', methods=['GET'])
def download_file():
    file_path = os.path.join('downloads', "updated_file.csv")
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return jsonify({"error": "File not found."}), 404

@app.route('/select_phone_column', methods=['POST'])
def select_phone_column():
    global current_data
    if current_data is None:
        return jsonify({"error": "No data loaded."}), 400

    data = request.json
    phone_column = data.get("phone_column")

    if phone_column not in current_data.columns:
        return jsonify({"error": f"Column '{phone_column}' not found in the dataset."}), 400

    # Fetch the selected phone column's data
    phone_numbers = current_data[phone_column].tolist()

    return jsonify({
        "message": f"Phone column '{phone_column}' selected successfully.",
        "phone_numbers": phone_numbers
    })

# @app.route('/process_phone_numbers', methods=['POST'])
# def process_phone_numbers():
#     global current_data
#     data = request.json
#     column = data.get('column')

#     if column not in current_data.columns:
#         return jsonify({"error": f"Column '{column}' not found in the dataset."}), 400

#     phone_numbers = current_data[column].fillna("").astype(str)  # Ensure column exists and is string

#     cleaned_numbers = []
#     countries = []

#     for phone in phone_numbers:
#         cleaned = None
#         country = None

#         # Turkey Patterns
#         if len(phone) == 10 and phone.startswith("5"):
#             cleaned = f"+90{phone}"
#             country = "Turkey"
#         elif len(phone) == 11 and phone.startswith("05"):
#             cleaned = f"+9{phone[1:]}"
#             country = "Turkey"
#         elif len(phone) == 12 and phone.startswith("90"):
#             cleaned = f"+{phone}"
#             country = "Turkey"
#         elif len(phone) == 10 and phone.startswith("85"):
#             cleaned = f"+90{phone}"
#             country = "Turkey"
#         elif len(phone) == 12 and phone.startswith("9085"):
#             cleaned = f"+{phone}"
#             country = "Turkey"

#         # Russia Patterns
#         elif len(phone) == 11 and phone.startswith("79"):
#             cleaned = f"+{phone}"
#             country = "Russia"
#         elif len(phone) == 11 and phone.startswith("89"):
#             cleaned = f"+79{phone[2:]}"  # Replace 89 with +79
#             country = "Russia"
        
#         # Kazakhstan Patterns
#         elif len(phone) == 11 and phone.startswith("87"):
#             cleaned = f"+77{phone[2:]}"  # Replace 87 with +77
#             country = "Kazakhstan"
#         elif len(phone) == 11 and phone.startswith("77"):
#             cleaned = f"+77{phone[2:]}"  # Keep +77 as is
#             country = "Kazakhstan"

#         # Luxembourg Patterns
#         elif len(phone) == 11 and phone.startswith(("99", "49")):
#             cleaned = f"+352{phone}"
#             country = "Luxembourg"
#         elif len(phone) == 14 and phone.startswith("352"):
#             cleaned = f"+{phone}"
#             country = "Luxembourg"

#         # Indonesia Patterns
#         elif len(phone) == 11 and phone.startswith(("98", "243", "240")):
#             cleaned = f"+62{phone}"
#             country = "Indonesia"
#         elif len(phone) == 13 and phone.startswith("62"):
#             cleaned = f"+{phone}"
#             country = "Indonesia"

#         # Germany Patterns
#         elif len(phone) == 11 and phone.startswith("97"):
#             cleaned = f"+49{phone}"
#             country = "Germany"
#         elif len(phone) == 13 and phone.startswith("4997"):
#             cleaned = f"+{phone}"
#             country = "Germany"
#         elif len(phone) == 11 and phone.startswith("6"):
#             cleaned = f"+49{phone}"
#             country = "Germany"

#         # US Patterns
#         elif len(phone) == 10 and phone.startswith("775"):
#             cleaned = f"+1{phone}"
#             country = "US"
#         elif len(phone) == 10 and phone.startswith("212"):
#             cleaned = f"+1{phone}"
#             country = "US"
#         elif len(phone) == 11 and phone.startswith("131"):
#             cleaned = f"+{phone}"
#             country = "US"

#         # Sweden Patterns
#         elif len(phone) == 8 and phone.startswith("185"):
#             cleaned = f"+46{phone}"
#             country = "Sweden"

#         # Taiwan Patterns
#         elif len(phone) == 11 and phone.startswith("100"):
#             cleaned = f"+886{phone}"
#             country = "Taiwan"

#         # South Africa Patterns
#         elif len(phone) == 11 and phone.startswith("27"):
#             cleaned = f"+{phone}"
#             country = "South Africa"

#         # Default Pattern (if no rules matched)
#         elif phone.startswith("+"):
#             cleaned = phone
#             country = "Unknown"

#         cleaned_numbers.append(cleaned)
#         countries.append(country)

#     # Add new columns to the DataFrame
#     current_data["Cleaned Phone Numbers"] = cleaned_numbers
#     current_data["Country"] = countries

#     # Prepare data for display
#     phone_data = [
#         {"original": original, "cleaned": cleaned, "country": country}
#         for original, cleaned, country in zip(phone_numbers, cleaned_numbers, countries)
#     ]

#     return jsonify({"phoneNumbers": phone_data})


@app.route('/process_phone_numbers', methods=['POST'])
def process_phone_numbers():
    global current_data
    if current_data is None:
        return jsonify({"error": "No data loaded."}), 400

    data = request.json
    phone_column = data.get("column")

    if phone_column not in current_data.columns:
        return jsonify({"error": f"Column '{phone_column}' not found in the dataset."}), 400

    phone_numbers = current_data[phone_column].fillna("").astype(str).tolist()
    processed_numbers = []

    cleaned_numbers = []
    countries = []

    for phone in phone_numbers:
        original = phone.strip()  # Keep the original phone number unchanged
        sanitized = re.sub(r'\D', '', phone)  # Remove all non-numeric characters

        # Turkey Patterns
        if len(sanitized) == 10 and sanitized.startswith("5"):
            cleaned = f"+90{sanitized}"
            country = "Turkey"
        elif len(sanitized) == 11 and sanitized.startswith("05"):
            cleaned = f"+9{sanitized[1:]}"
            country = "Turkey"
        elif len(sanitized) == 12 and sanitized.startswith("90"):
            cleaned = f"+{sanitized}"
            country = "Turkey"
        elif len(sanitized) == 10 and sanitized.startswith("85"):
            cleaned = f"+90{sanitized}"
            country = "Turkey"
        elif len(sanitized) == 12 and sanitized.startswith("9085"):
            cleaned = f"+{sanitized}"
            country = "Turkey"

        # Russia Patterns
        elif len(sanitized) == 11 and sanitized.startswith("79"):
            cleaned = f"+{sanitized}"
            country = "Russia"
        elif len(sanitized) == 11 and sanitized.startswith("89"):
            cleaned = f"+79{sanitized[2:]}"  # Replace 89 with +79
            country = "Russia"
        elif len(sanitized) == 11 and sanitized.startswith("84"):
            cleaned = f"+74{sanitized[2:]}"  # Replace 89 with +79
            country = "Russia"
        elif len(sanitized) == 11 and sanitized.startswith("88"):
            cleaned = f"+78{sanitized[2:]}"  # Replace 89 with +79
            country = "Russia"
        
        # Kazakhstan Patterns
        elif len(sanitized) == 11 and sanitized.startswith("87"):
            cleaned = f"+77{sanitized[2:]}"  # Replace 87 with +77
            country = "Kazakhstan"
        elif len(sanitized) == 11 and sanitized.startswith("77"):
            cleaned = f"+77{sanitized[2:]}"  # Keep +77 as is
            country = "Kazakhstan"

        # Luxembourg Patterns
        elif len(sanitized) == 11 and sanitized.startswith(("99", "49")):
            cleaned = f"+352{sanitized}"
            country = "Luxembourg"
        elif len(sanitized) == 14 and sanitized.startswith("352"):
            cleaned = f"+{sanitized}"
            country = "Luxembourg"

        # Indonesia Patterns
        elif len(sanitized) == 11 and sanitized.startswith(("98", "243", "240")):
            cleaned = f"+62{sanitized}"
            country = "Indonesia"
        elif len(sanitized) == 13 and sanitized.startswith("62"):
            cleaned = f"+{sanitized}"
            country = "Indonesia"

        # Germany Patterns
        elif len(sanitized) == 11 and sanitized.startswith("97"):
            cleaned = f"+49{sanitized}"
            country = "Germany"
        elif len(sanitized) == 13 and sanitized.startswith("4997"):
            cleaned = f"+{sanitized}"
            country = "Germany"
        elif len(sanitized) == 11 and sanitized.startswith("6"):
            cleaned = f"+49{sanitized}"
            country = "Germany"

        # US Patterns
        elif len(sanitized) == 10 and sanitized.startswith("775"):
            cleaned = f"+1{sanitized}"
            country = "US"
        elif len(sanitized) == 10 and sanitized.startswith("212"):
            cleaned = f"+1{sanitized}"
            country = "US"
        elif len(sanitized) == 11 and sanitized.startswith("131"):
            cleaned = f"+{sanitized}"
            country = "US"

        # Sweden Patterns
        elif len(sanitized) == 8 and sanitized.startswith("185"):
            cleaned = f"+46{sanitized}"
            country = "Sweden"

        # Taiwan Patterns
        elif len(sanitized) == 11 and sanitized.startswith("100"):
            cleaned = f"+886{sanitized}"
            country = "Taiwan"

        # South Africa Patterns
        elif len(sanitized) == 11 and sanitized.startswith("27"):
            cleaned = f"+{sanitized}"
            country = "South Africa"

        # Default Pattern (if no rules matched)
        elif sanitized.startswith("+"):
            cleaned = sanitized
            country = "Unknown"

        else:
            cleaned = 'N/A'
            country = 'N/A'

        cleaned_numbers.append(cleaned if cleaned != 'N/A' else original)
        countries.append(country)

        processed_numbers.append({
            "original": original,
            "cleaned": cleaned if cleaned != 'N/A' else original,  # Keep original if not matched
            "country": country
        })
    
    # Add new columns to the dataframe
    current_data["Cleaned Phone Numbers"] = cleaned_numbers
    current_data["Country"] = countries
    
    return jsonify({"phoneNumbers": processed_numbers})


if __name__ == '__main__':
    app.run(debug=True)
