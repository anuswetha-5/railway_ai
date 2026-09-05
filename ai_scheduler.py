import os
import sqlite3
import random
from datetime import datetime

print("Waking up the Smart Scheduler Engine...\n")

# Bulletproof path lookup
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database", "railway_planning.db")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

while True:
    # Get the highest-priority pending task
    cursor.execute('''
        SELECT job_id, track_id, min_duration_needed, task, department, ai_score
        FROM Jobs
        WHERE status IS NULL OR status = 'Pending'
        ORDER BY ai_score DESC
        LIMIT 1
    ''')
    top_job = cursor.fetchone()

    if not top_job:
        print("\nAll tracks are perfectly maintained! No pending jobs found.")
        break
    else:
        main_job_id, target_track, needed_time, task_name, dept, score = top_job
        print(f"\n🎯 TARGET ACQUIRED: {main_job_id} ({task_name})")
        print(f"   Track: {target_track} | AI Score: {score}/100 | Needed Time: {needed_time} mins")
        
        # Fetch ALL windows chronologically
        cursor.execute('''
            SELECT availability_id, window_start, window_end
            FROM Corridor_Availability
            WHERE track_id = ?
            ORDER BY datetime(window_start) ASC;
        ''', (target_track,))
        all_windows = cursor.fetchall()
        
        suitable_window = None
        available_minutes = 0
        
        # Loop to verify if a window actually fits the job length requirement
        for win in all_windows:
            avail_id, block_start, block_end = win
            
            fmt = "%Y-%m-%d %H:%M"
            try:
                start_dt = datetime.strptime(block_start, fmt)
                end_dt = datetime.strptime(block_end, fmt)
                window_minutes = (end_dt - start_dt).total_seconds() / 60
                
                if window_minutes >= needed_time:
                    suitable_window = win
                    available_minutes = window_minutes
                    break  # Found the earliest valid window that fits!
            except Exception:
                continue

        # If absolutely no windows on this track fit the job's duration requirement
        if not suitable_window:
            print(f"   ❌ No empty gaps found long enough for {target_track}. Marking as Delayed.")
            cursor.execute("UPDATE Jobs SET status = 'Delayed' WHERE job_id = ?", (main_job_id,))
            conn.commit()
            continue
        else:
            avail_id, block_start, block_end = suitable_window
            print(f"   Found matching empty window: {block_start} to {block_end} ({available_minutes} mins total)")
            
            # Calculate remaining space after scheduling the main job
            remaining_minutes = available_minutes - needed_time
            
            # 👑 SMART FIX: Pull other jobs on this track and track min_duration_needed
            cursor.execute('''
                SELECT job_id, task, department, min_duration_needed FROM Jobs
                WHERE track_id = ? AND job_id != ? AND (status IS NULL OR status = 'Pending')
                ORDER BY ai_score DESC;
            ''', (target_track, main_job_id))
            other_jobs = cursor.fetchall()

            valid_shadow_jobs = []
            for oj_id, oj_task, oj_dept, oj_duration in other_jobs:
                # Only bundle the job if it physically fits in the remaining window space!
                if oj_duration <= remaining_minutes:
                    valid_shadow_jobs.append((oj_id, oj_task, oj_dept))
                    remaining_minutes -= oj_duration  # Deduct time from the window pool
                    print(f"      [Time Cushion] Bundled secondary job {oj_id}. Time left in window: {remaining_minutes} mins")

            new_block_id = f"BLK-AI-{main_job_id[-4:]}"
            total_depts = 1 + len(valid_shadow_jobs)
            
            # Insert into Block_Register using unified column naming schema
            cursor.execute('''
                INSERT OR REPLACE INTO Block_Register (block_id, track_id, window_start, window_end, total_departments_involved)
                VALUES (?, ?, ?, ?, ?)
            ''', (new_block_id, target_track, block_start, block_end, total_depts))
            
            cursor.execute("INSERT INTO Block_Jobs (block_id, job_id) VALUES (?, ?)", (new_block_id, main_job_id))
            cursor.execute("UPDATE Jobs SET status = 'Scheduled', scheduled_start = ?, scheduled_end = ? WHERE job_id = ?", (block_start, block_end, main_job_id))

            print(f"   Executing Shadow Block... Safely bundled {len(valid_shadow_jobs)} additional jobs inside window limit.")
            for oj_id, oj_task, oj_dept in valid_shadow_jobs:
                cursor.execute("INSERT INTO Block_Jobs (block_id, job_id) VALUES (?, ?)", (new_block_id, oj_id))
                cursor.execute("UPDATE Jobs SET status = 'Scheduled', scheduled_start = ?, scheduled_end = ? WHERE job_id = ?", (block_start, block_end, oj_id))
                print(f"      + Safe Bundle locked: {oj_id} ({oj_task} - {oj_dept})")

            # Remove window from pool so it doesn't get double-booked by separate task pipelines
            cursor.execute("DELETE FROM Corridor_Availability WHERE availability_id = ?;", (avail_id,))

            conn.commit()
            print(f"   SUCCESS: {new_block_id} permanently saved to the database.")    

conn.close()
