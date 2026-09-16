import database as db
import json
from dotenv import load_dotenv
load_dotenv()

with db._get_connection() as conn:
    with conn.cursor() as cur:
        # Get all LEFT_GROUP events
        cur.execute("""
            SELECT h.user_id, h.created_at, h.details, s.user_metadata_json
            FROM user_history h
            LEFT JOIN screening_sessions s ON h.user_id = s.user_id
            WHERE h.event_type = 'LEFT_GROUP'
            ORDER BY h.created_at DESC
        """)
        rows = cur.fetchall()
        print(f"Total LEFT_GROUP events: {len(rows)}")
        print("-" * 60)
        
        seen_users = set()
        for row in rows:
            meta = json.loads(row['user_metadata_json'] or '{}') if row.get('user_metadata_json') else {}
            name = meta.get('full_name', 'Unknown')
            username = f"@{meta.get('username')}" if meta.get('username') else 'N/A'
            duplicate = " ⚠️ DUPLICATE" if row['user_id'] in seen_users else ""
            seen_users.add(row['user_id'])
            print(f"ID: {row['user_id']} | Name: {name} | Username: {username} | Date: {row['created_at']}{duplicate}")
        
        print(f"\nUnique users who left: {len(seen_users)}")
        print(f"Total events (including duplicates): {len(rows)}")
