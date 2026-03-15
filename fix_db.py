import sqlite3
import os

def fix():
    db_path = "data.db"
    if not os.path.exists(db_path):
        print("Database file not found. Nothing to fix.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    columns_to_add = [
        ("hr_data", "JSON"),
        ("activity_data", "JSON"),
        ("streams_data", "JSON")
    ]

    for col_name, col_type in columns_to_add:
        try:
            print(f"Adding column {col_name} to fixable_activities...")
            cursor.execute(f"ALTER TABLE fixable_activities ADD COLUMN {col_name} {col_type}")
            print(f"  Column {col_name} added successfully.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print(f"  Column {col_name} already exists.")
            else:
                print(f"  Error adding {col_name}: {e}")

    conn.commit()
    conn.close()
    print("\nDatabase schema update complete!")

if __name__ == "__main__":
    fix()
