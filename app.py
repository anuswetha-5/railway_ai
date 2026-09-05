import sqlite3
import pandas as pd
import streamlit as st
from datetime import datetime, date, timedelta

DB_PATH = "database/railway_planning.db"


@st.cache_resource
def get_shared_connection():
    """A single, shared connection reused across reruns/tabs instead of
    opening a new sqlite3 connection on every call — this is what was
    causing 'database is locked' under Streamlit's frequent reruns."""
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    # WAL lets readers and writers work without blocking each other.
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def get_connection():
    return get_shared_connection()


def run_scheduler_pipeline():
    """Automated scheduling loop executing Shadow Block bundling with Time Logic."""
    conn = get_connection()
    cursor = conn.cursor()
    blocks_created = 0

    while True:
        cursor.execute('''
            SELECT job_id, track_id, min_duration_needed, task, department, ai_score 
            FROM Jobs 
            WHERE status IS NULL OR status = 'Pending' 
            ORDER BY ai_score DESC 
            LIMIT 1
        ''')
        top_job = cursor.fetchone()

        if not top_job:
            break

        main_job_id, target_track, needed_time, task_name, dept, score = top_job

        cursor.execute('''
            SELECT availability_id, window_start, window_end 
            FROM Corridor_Availability 
            WHERE track_id = ?
            LIMIT 1
        ''', (target_track,))
        window = cursor.fetchone()

        if not window:
            cursor.execute("UPDATE Jobs SET status = 'Delayed' WHERE job_id = ?", (main_job_id,))
            conn.commit()
            continue

        avail_id, block_start, block_end = window

        
        try:
            start_dt = datetime.strptime(block_start, "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(block_end, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            start_dt = datetime.strptime(block_start, "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(block_end, "%Y-%m-%d %H:%M")

        available_mins = (end_dt - start_dt).total_seconds() / 60.0

        if needed_time > available_mins:
            cursor.execute("UPDATE Jobs SET status = 'Delayed' WHERE job_id = ?", (main_job_id,))
            conn.commit()
            continue

        cursor.execute('''
            SELECT job_id, task, department, min_duration_needed FROM Jobs 
            WHERE track_id = ? AND job_id != ? AND (status IS NULL OR status = 'Pending')
        ''', (target_track, main_job_id))
        other_jobs = cursor.fetchall()

        valid_shadow_jobs = []
        for oj in other_jobs:
            oj_id, oj_task, oj_dept, oj_time = oj
            if oj_time <= available_mins:
                valid_shadow_jobs.append(oj_id)

        new_block_id = f"BLK-AI-{main_job_id[-4:]}"
        total_depts = 1 + len(valid_shadow_jobs)

        try:
            cursor.execute('''
                INSERT INTO Block_Register (block_id, track_id, window_start, window_end, total_departments_involved)
                VALUES (?, ?, ?, ?, ?)
            ''', (new_block_id, target_track, block_start, block_end, total_depts))
        except sqlite3.OperationalError:
            try:
                cursor.execute('''
                    INSERT INTO Block_Register (block_id, track_id, window_start, window_end, total_departments)
                    VALUES (?, ?, ?, ?, ?)
                ''', (new_block_id, target_track, block_start, block_end, total_depts))
            except sqlite3.OperationalError:
                cursor.execute('''
                    INSERT INTO Block_Register (block_id, track_id, window_start, window_end)
                    VALUES (?, ?, ?, ?)
                ''', (new_block_id, target_track, block_start, block_end))

        cursor.execute("INSERT INTO Block_Jobs (block_id, job_id) VALUES (?, ?)", (new_block_id, main_job_id))
        cursor.execute("UPDATE Jobs SET status = 'Scheduled' WHERE job_id = ?", (main_job_id,))

        for valid_id in valid_shadow_jobs:
            cursor.execute("INSERT INTO Block_Jobs (block_id, job_id) VALUES (?, ?)", (new_block_id, valid_id))
            cursor.execute("UPDATE Jobs SET status = 'Scheduled' WHERE job_id = ?", (valid_id,))

        conn.commit()
        blocks_created += 1

    return blocks_created


def reset_database():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM Block_Jobs")
    cur.execute("DELETE FROM Block_Register")
    cur.execute("UPDATE Jobs SET status = 'Pending'")
    conn.commit()



st.set_page_config(
    page_title="AI Rail Corridor Scheduler",
    layout="wide",
    page_icon="🚆",
    initial_sidebar_state="expanded",
)


st.markdown("""
<style>

    /* Overall page */
    .main .block-container {
        padding-top: 2rem;
        padding-bottom: 3rem;
        max-width: 1200px;
    }

    /* Header */
    .app-header {
        display: flex;
        align-items: center;
        gap: 0.9rem;
        margin-bottom: 0.1rem;
    }
    .app-header .icon-box {
        background: linear-gradient(135deg, #1f4e8c, #2f6fbf);
        width: 52px; height: 52px;
        border-radius: 14px;
        display: flex; align-items: center; justify-content: center;
        font-size: 26px;
        flex-shrink: 0;
    }
    .app-header h1 {
        font-size: 1.7rem;
        font-weight: 700;
        margin: 0;
        line-height: 1.2;
    }
    .app-subtitle {
        color: rgba(150,150,150,0.9);
        font-size: 0.95rem;
        margin-top: 2px;
    }

    /* KPI cards */
    div[data-testid="stMetric"] {
        background: rgba(127, 127, 127, 0.07);
        border: 1px solid rgba(127, 127, 127, 0.15);
        border-radius: 12px;
        padding: 1rem 1.1rem 0.8rem 1.1rem;
        transition: border-color 0.15s ease;
    }
    div[data-testid="stMetric"]:hover {
        border-color: rgba(47, 111, 191, 0.5);
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.8rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        opacity: 0.75;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.9rem;
        font-weight: 700;
    }

    /* Section headers */
    .section-title {
        font-size: 1.05rem;
        font-weight: 700;
        margin: 0.2rem 0 0.8rem 0;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }

    /* Sidebar */
    section[data-testid="stSidebar"] .block-container {
        padding-top: 1.5rem;
    }
    section[data-testid="stSidebar"] h2 {
        font-size: 1rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.03em;
        opacity: 0.85;
        margin-bottom: 0.6rem;
    }

    /* Tabs */
    button[data-baseweb="tab"] {
        font-weight: 600;
        font-size: 0.95rem;
    }

    /* Dataframes */
    div[data-testid="stDataFrame"] {
        border-radius: 10px;
        overflow: hidden;
    }

    /* Buttons */
    div.stButton > button {
        border-radius: 9px;
        font-weight: 600;
    }

    hr { margin: 1.2rem 0; }
</style>
""", unsafe_allow_html=True)


st.markdown("""
<div class="app-header">
    <div class="icon-box">🚆</div>
    <div>
        <h1>Intelligent Railway Corridor Maintenance Engine</h1>
        <div class="app-subtitle">Real-time conflict-free slot allocation &amp; cross-departmental shadow bundling</div>
    </div>
</div>
""", unsafe_allow_html=True)

st.write("")


conn = get_connection()
total_jobs = pd.read_sql_query("SELECT COUNT(*) as c FROM Jobs", conn)["c"][0]
pending_jobs = pd.read_sql_query("SELECT COUNT(*) as c FROM Jobs WHERE status IS NULL OR status = 'Pending'", conn)["c"][0]
scheduled_jobs = pd.read_sql_query("SELECT COUNT(*) as c FROM Jobs WHERE status = 'Scheduled'", conn)["c"][0]
delayed_jobs = pd.read_sql_query("SELECT COUNT(*) as c FROM Jobs WHERE status = 'Delayed'", conn)["c"][0]
total_blocks = pd.read_sql_query("SELECT COUNT(*) as c FROM Block_Register", conn)["c"][0]

kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
kpi1.metric("Total Jobs", total_jobs)
kpi2.metric("Pending", pending_jobs)
kpi3.metric("Scheduled", scheduled_jobs)
kpi4.metric("Delayed", delayed_jobs)
kpi5.metric("Blocks", total_blocks)

st.divider()



with st.sidebar:
    st.header("⚙️ Operations")

    if st.button("▶ Run AI Auto-Scheduler", type="primary", use_container_width=True):
        with st.spinner("Analyzing corridor gaps and bundling jobs..."):
            count = run_scheduler_pipeline()
        if count > 0:
            st.success(f"Generated {count} consolidated block(s).")
        else:
            st.info("No pending jobs eligible for open gaps.")
        st.rerun()

    if st.button("🔄 Reset Demo Database", use_container_width=True):
        reset_database()
        st.warning("Database reset — all jobs set back to 'Pending'.")
        st.rerun()

    st.divider()
    st.header("📝 New Work Order")

    with st.form("new_job_form", clear_on_submit=True):
        import random
        # Auto-generate a unique ID every time the form loads
        auto_id = f"JOB-{random.randint(5000, 99999)}"
        
        j_id = st.text_input("Job ID", value=auto_id)
        t_id = st.text_input("Track ID", value="TRK-121")
        dept = st.selectbox("Department", ["Civil", "Electrical", "S&T", "Traffic"])
        task = st.text_input("Task Description", value="Ballast Shoulder Cleaning")
        severity = st.selectbox("Defect Severity", ["Low", "Medium", "High", "Critical"], index=1)
        duration = st.number_input("Duration (min)", min_value=15, max_value=480, value=60)
        req_date = st.date_input("Request Date", value=date.today())
        deadline_date = st.date_input("Deadline", value=date.today() + timedelta(days=7))
        score = st.slider("Priority Score", min_value=1.0, max_value=100.0, value=75.0)

        submitted = st.form_submit_button("Submit Work Order", use_container_width=True, type="primary")
        
        if submitted:
            if not j_id:
                st.error("Please enter a valid Job ID.")
            elif deadline_date < req_date:
                st.error("Deadline cannot be before the request date.")
            else:
                c = get_connection()
                job_saved = False  # Track success manually
                
                try:
                    cur = c.cursor()
                    cur.execute("""
                        INSERT INTO Jobs (
                            job_id, track_id, department, task, defect_severity,
                            min_duration_needed, request_date, deadline, status, ai_score
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?)
                    """, (
                        j_id, t_id, dept, task, severity, duration,
                        req_date.strftime("%Y-%m-%d"), deadline_date.strftime("%Y-%m-%d"), score
                    ))
                    c.commit()
                    st.success(f"Dispatched {j_id} to triage queue.")
                    job_saved = True  # Mark as successful!
                    
                except sqlite3.IntegrityError as err:
                    if "UNIQUE constraint failed" in str(err):
                        st.error(f"⚠️ Job ID '{j_id}' is already taken! Please use a different ID.")
                    else:
                        st.error(f"Could not save job: {err}")
                
                # Only rerun the app to clear the form if the job was actually saved
                if job_saved:
                    import time
                    time.sleep(0.5) # Quick half-second pause so the user sees the green success message
                    st.rerun()


tab1, tab2, tab3 = st.tabs(["Shadow Blocks", "Job Queue", "Corridor Gaps"])

conn = get_connection()

with tab1:
    st.markdown('<div class="section-title">Consolidated Maintenance Blocks</div>', unsafe_allow_html=True)

    blocks_df = pd.read_sql_query("""
        SELECT 
            b.block_id AS 'Block ID',
            b.track_id AS 'Track',
            b.window_start AS 'Window Start',
            b.window_end AS 'Window End',
            COUNT(bj.job_id) AS 'Bundled Tasks'
        FROM Block_Register b
        LEFT JOIN Block_Jobs bj ON b.block_id = bj.block_id
        GROUP BY b.block_id
        ORDER BY b.window_start ASC
    """, conn)

    if blocks_df.empty:
        st.info("No blocks registered yet. Use **Run AI Auto-Scheduler** in the sidebar to generate them.")
    else:
        st.dataframe(blocks_df, use_container_width=True, hide_index=True)

        st.markdown('<div class="section-title">Block Task Mapping</div>', unsafe_allow_html=True)
        selected_block = st.selectbox("Select a block to inspect its bundled tasks", blocks_df["Block ID"].unique())
        if selected_block:
            details_df = pd.read_sql_query("""
                SELECT 
                    j.job_id AS 'Job ID',
                    j.department AS 'Department',
                    j.task AS 'Task Details',
                    j.min_duration_needed AS 'Duration (m)',
                    j.ai_score AS 'Priority Score'
                FROM Block_Jobs bj
                JOIN Jobs j ON bj.job_id = j.job_id
                WHERE bj.block_id = ?
            """, conn, params=(selected_block,))
            st.dataframe(details_df, use_container_width=True, hide_index=True)

with tab2:
    st.markdown('<div class="section-title">Job Priority &amp; Scheduling Status</div>', unsafe_allow_html=True)

    status_filter = st.radio(
        "Filter by status", ["All", "Pending", "Scheduled", "Delayed"],
        horizontal=True, label_visibility="collapsed"
    )

    query = """
        SELECT 
            job_id AS 'Job ID',
            track_id AS 'Track',
            department AS 'Department',
            task AS 'Task',
            defect_severity AS 'Severity',
            min_duration_needed AS 'Duration (m)',
            request_date AS 'Requested',
            deadline AS 'Deadline',
            ai_score AS 'AI Score',
            COALESCE(status, 'Pending') AS 'Status'
        FROM Jobs
    """
    if status_filter != "All":
        query += f" WHERE COALESCE(status, 'Pending') = '{status_filter}'"
    query += " ORDER BY ai_score DESC"

    jobs_df = pd.read_sql_query(query, conn)
    st.dataframe(
        jobs_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "AI Score": st.column_config.ProgressColumn(
                "AI Score", min_value=0, max_value=100, format="%.0f"
            ),
        },
    )

with tab3:
    st.markdown('<div class="section-title">Available Corridor Timetables</div>', unsafe_allow_html=True)

    avail_df = pd.read_sql_query("""
        SELECT 
            availability_id AS 'Window ID',
            track_id AS 'Track',
            window_start AS 'Available From',
            window_end AS 'Available Until'
        FROM Corridor_Availability
        ORDER BY window_start ASC
    """, conn)
    st.dataframe(avail_df, use_container_width=True, hide_index=True)