import os
import sqlite3
import random
import pandas as pd
import streamlit as st
import altair as alt
from datetime import datetime, timedelta


# ============================================================
# DATABASE PATH
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database", "railway_planning.db")


# ============================================================
# STREAMLIT PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="Railway AI Engine",
    layout="wide",
    page_icon="🚆",
    initial_sidebar_state="expanded"
)


# ============================================================
# CUSTOM PREMIUM THEME
# ============================================================

st.markdown(
    """
    <style>

        /* Main application background */
        .stApp {
            background-color: #0F172A;
            color: #F8FAFC;
        }

        /* Main heading */
        h1 {
            color: #06B6D4 !important;
            font-family: 'Helvetica Neue', sans-serif;
            font-weight: 800;
        }

        /* Sub headings */
        h3, h4 {
            color: #38BDF8 !important;
        }

        /* Metric values */
        [data-testid="stMetricValue"] {
            color: #F59E0B !important;
            font-weight: bold;
            font-size: 2.2rem !important;
        }

        /* Metric labels */
        [data-testid="stMetricLabel"] {
            color: #94A3B8 !important;
            text-transform: uppercase;
            letter-spacing: 0.1rem;
        }

        /* Sidebar */
        section[data-testid="stSidebar"] {
            background-color: #1E293B !important;
            border-right: 2px solid #334155;
        }

        /* Buttons */
        .stButton > button {
            background-color: #06B6D4 !important;
            color: #ffffff !important;
            font-weight: bold;
            border-radius: 8px;
            border: none;
            transition: all 0.3s ease;
        }

        .stButton > button:hover {
            background-color: #0891B2 !important;
            transform: scale(1.02);
            box-shadow: 0 4px 15px rgba(6, 182, 212, 0.4);
        }

    </style>
    """,
    unsafe_allow_html=True
)


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_db_connection():
    return sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )


# ============================================================
# AI JOB SCORING
# ============================================================

def calculate_single_job_score(
    severity,
    track_age,
    track_crit,
    deadline_str
):
    score = 0

    # Defect severity
    if severity == "Critical":
        score += 40
    elif severity == "High":
        score += 25
    elif severity == "Medium":
        score += 15
    else:
        score += 5

    # Track age
    if track_age > 25:
        score += 20
    elif track_age > 15:
        score += 10

    # Track criticality
    if track_crit == "Critical":
        score += 20
    elif track_crit == "High":
        score += 10

    # Deadline urgency
    try:
        days_left = (
            datetime.strptime(
                deadline_str,
                "%Y-%m-%d"
            ).date()
            - datetime.now().date()
        ).days

        if days_left <= 2:
            score += 20
        elif days_left <= 5:
            score += 10

    except Exception:
        pass

    return min(score, 100)


# ============================================================
# LIVE AI SCHEDULER
# ============================================================

def run_live_scheduler():

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get all pending jobs
    cursor.execute(
        """
        SELECT
            job_id,
            track_id,
            min_duration_needed
        FROM Jobs
        WHERE status = 'Pending'
        ORDER BY ai_score DESC;
        """
    )

    pending = cursor.fetchall()

    for job in pending:

        j_id, t_id, needed = job

        # Find available windows for this track
        cursor.execute(
            """
            SELECT
                availability_id,
                window_start,
                window_end
            FROM Corridor_Availability
            WHERE track_id = ?
            ORDER BY datetime(window_start) ASC;
            """,
            (t_id,)
        )

        windows = cursor.fetchall()

        assigned = None

        # Search for suitable time window
        for win in windows:

            w_id, start, end = win

            try:

                win_mins = (
                    datetime.strptime(
                        end,
                        "%Y-%m-%d %H:%M"
                    )
                    -
                    datetime.strptime(
                        start,
                        "%Y-%m-%d %H:%M"
                    )
                ).total_seconds() / 60

                if win_mins >= needed:
                    assigned = win
                    break

            except Exception:
                continue

        # If suitable window found
        if assigned:

            w_id, start, end = assigned

            # Generate block ID
            b_id = f"BLK-AI-{random.randint(1000, 9999)}"

            # Create block
            cursor.execute(
                """
                INSERT OR REPLACE INTO Block_Register
                (
                    block_id,
                    track_id,
                    window_start,
                    window_end,
                    total_departments_involved
                )
                VALUES (?, ?, ?, ?, 1);
                """,
                (
                    b_id,
                    t_id,
                    start,
                    end
                )
            )

            # Connect job to block
            cursor.execute(
                """
                INSERT INTO Block_Jobs
                (
                    block_id,
                    job_id
                )
                VALUES (?, ?);
                """,
                (
                    b_id,
                    j_id
                )
            )

            # Update job
            cursor.execute(
                """
                UPDATE Jobs
                SET
                    status = 'Scheduled',
                    scheduled_start = ?,
                    scheduled_end = ?
                WHERE job_id = ?;
                """,
                (
                    start,
                    end,
                    j_id
                )
            )

            # Remove used availability
            cursor.execute(
                """
                DELETE FROM Corridor_Availability
                WHERE availability_id = ?;
                """,
                (w_id,)
            )

        # No suitable window
        else:

            cursor.execute(
                """
                UPDATE Jobs
                SET status = 'Delayed'
                WHERE job_id = ?;
                """,
                (j_id,)
            )

    conn.commit()
    conn.close()


# ============================================================
# MAIN APPLICATION HEADER
# ============================================================

st.title(
    "🚆 Intelligent Railway Corridor Maintenance Engine"
)

st.caption(
    "Real-time conflict-free slot allocation "
    "& cross-departmental shadow bundling"
)

st.markdown(
    "<hr style='border: 1px solid #334155;'>",
    unsafe_allow_html=True
)


# ============================================================
# SIDEBAR - SIMULATION CENTER
# ============================================================

st.sidebar.markdown(
    "<h2 style='color:#06B6D4;'>🕹️ Simulation Control</h2>",
    unsafe_allow_html=True
)


# ============================================================
# LOAD TRACK DATA
# ============================================================

conn = get_db_connection()

tracks_df = pd.read_sql_query(
    """
    SELECT
        track_id,
        section_name,
        track_age,
        criticality_level
    FROM Tracks
    """,
    conn
)

conn.close()


# ============================================================
# ADD NEW JOB FORM
# ============================================================

with st.sidebar.form("add_job_form"):

    st.markdown(
        "<b style='color:#38BDF8;'>"
        "Simulate New Repair Request"
        "</b>",
        unsafe_allow_html=True
    )

    # Track selection
    if not tracks_df.empty:

        selected_track = st.selectbox(
            "Select Target Track Section",
            tracks_df["track_id"]
            + " - "
            + tracks_df["section_name"]
        )

        t_id = selected_track.split(" - ")[0]

    else:

        st.warning("No track data found in database.")
        t_id = None

    # Department
    dept = st.selectbox(
        "Requesting Department",
        [
            "Engineering",
            "Electrical",
            "S&T",
            "Traffic"
        ]
    )

    # Task
    task = st.text_input(
        "Task Description",
        "Emergency Rail Weld Repair"
    )

    # Severity
    sev = st.selectbox(
        "Defect Severity",
        [
            "Low",
            "Medium",
            "High",
            "Critical"
        ]
    )

    # Duration
    dur = st.slider(
        "Duration Needed (mins)",
        30,
        180,
        60,
        step=15
    )

    # Deadline
    days_out = st.slider(
        "Deadline Timeline (Days)",
        1,
        10,
        3
    )

    submit_job = st.form_submit_button(
        "➕ Inject Request into Pipeline"
    )


# ============================================================
# PROCESS NEW JOB
# ============================================================

if submit_job and t_id is not None:

    target_track_meta = tracks_df[
        tracks_df["track_id"] == t_id
    ].iloc[0]

    deadline_date = (
        datetime.now()
        + timedelta(days=days_out)
    ).strftime("%Y-%m-%d")

    req_date = datetime.now().strftime(
        "%Y-%m-%d"
    )

    # Generate job ID
    new_job_id = f"JOB-{random.randint(9000, 9999)}"

    # Calculate AI priority score
    calculated_ai_score = calculate_single_job_score(
        sev,
        target_track_meta["track_age"],
        target_track_meta["criticality_level"],
        deadline_date
    )

    # Insert job
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO Jobs
        (
            job_id,
            track_id,
            department,
            task,
            min_duration_needed,
            defect_severity,
            request_date,
            deadline,
            ai_score,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending')
        """,
        (
            new_job_id,
            t_id,
            dept,
            task,
            dur,
            sev,
            req_date,
            deadline_date,
            calculated_ai_score
        )
    )

    conn.commit()
    conn.close()

    # Success message
    st.sidebar.markdown(
        f"""
        <div style='
            padding:10px;
            background-color:#1E293B;
            border-left:4px solid #10B981;
            color:#10B981;
            font-weight:bold;
        '>
            🎉 Success! {new_job_id}
            injected with Score:
            {calculated_ai_score}/100
        </div>
        """,
        unsafe_allow_html=True
    )


# ============================================================
# AI AUTO-SCHEDULER BUTTON
# ============================================================

st.sidebar.markdown(
    "<br><hr style='border: 1px solid #334155;'><br>",
    unsafe_allow_html=True
)

st.sidebar.markdown(
    "<b style='color:#38BDF8;'>Allocation Controller</b>",
    unsafe_allow_html=True
)


if st.sidebar.button(
    "🚀 Run AI Auto-Scheduler",
    use_container_width=True
):

    with st.spinner(
        "AI Algorithm processing constraint "
        "satisfaction loops..."
    ):

        run_live_scheduler()

    st.sidebar.markdown(
        """
        <div style='
            padding:10px;
            background-color:#1E293B;
            border-left:4px solid #06B6D4;
            color:#06B6D4;
            font-weight:bold;
        '>
            🎯 Execution Cycle Complete!
        </div>
        """,
        unsafe_allow_html=True
    )


# ============================================================
# DASHBOARD DATA
# ============================================================

conn = get_db_connection()

jobs_df = pd.read_sql_query(
    "SELECT * FROM Jobs",
    conn
)

gaps_count = pd.read_sql_query(
    """
    SELECT COUNT(*) as count
    FROM Corridor_Availability
    """,
    conn
)["count"].iloc[0]

conn.close()


# ============================================================
# KPI METRIC CARDS
# ============================================================

m1, m2, m3, m4, m5 = st.columns(5)


# Total Jobs
with m1:

    st.markdown(
        """
        <div style='
            background-color:#1E293B;
            padding:15px;
            border-radius:10px;
            border-bottom:4px solid #3B82F6;
        '>
        """,
        unsafe_allow_html=True
    )

    st.metric(
        "Total Jobs Pool",
        len(jobs_df)
    )

    st.markdown(
        "</div>",
        unsafe_allow_html=True
    )


# Pending Jobs
with m2:

    st.markdown(
        """
        <div style='
            background-color:#1E293B;
            padding:15px;
            border-radius:10px;
            border-bottom:4px solid #EF4444;
        '>
        """,
        unsafe_allow_html=True
    )

    st.metric(
        "Pending Queue",
        len(
            jobs_df[
                jobs_df["status"] == "Pending"
            ]
        )
    )

    st.markdown(
        "</div>",
        unsafe_allow_html=True
    )


# Scheduled Jobs
with m3:

    st.markdown(
        """
        <div style='
            background-color:#1E293B;
            padding:15px;
            border-radius:10px;
            border-bottom:4px solid #10B981;
        '>
        """,
        unsafe_allow_html=True
    )

    st.metric(
        "Scheduled Blocks",
        len(
            jobs_df[
                jobs_df["status"] == "Scheduled"
            ]
        )
    )

    st.markdown(
        "</div>",
        unsafe_allow_html=True
    )


# Delayed Jobs
with m4:

    st.markdown(
        """
        <div style='
            background-color:#1E293B;
            padding:15px;
            border-radius:10px;
            border-bottom:4px solid #64748B;
        '>
        """,
        unsafe_allow_html=True
    )

    st.metric(
        "Delayed / Blocked",
        len(
            jobs_df[
                jobs_df["status"] == "Delayed"
            ]
        )
    )

    st.markdown(
        "</div>",
        unsafe_allow_html=True
    )


# Available Slots
with m5:

    st.markdown(
        """
        <div style='
            background-color:#1E293B;
            padding:15px;
            border-radius:10px;
            border-bottom:4px solid #06B6D4;
        '>
        """,
        unsafe_allow_html=True
    )

    st.metric(
        "Safe Slots Left",
        gaps_count
    )

    st.markdown(
        "</div>",
        unsafe_allow_html=True
    )


# ============================================================
# ANALYTICS SECTION
# ============================================================

st.markdown(
    "<br>### 📊 Operational Risk & Queue Analytics",
    unsafe_allow_html=True
)

v1, v2 = st.columns(2)


# ============================================================
# CHART 1 - AI SCORING BY DEPARTMENT
# ============================================================

with v1:

    st.markdown(
        "#### AI Scoring Distribution by Department"
    )

    if not jobs_df.empty:

        chart1 = (
            alt.Chart(jobs_df)
            .mark_bar(
                cornerRadiusTopLeft=5,
                cornerRadiusTopRight=5
            )
            .encode(
                x=alt.X(
                    "department:N",
                    title="Department",
                    axis=alt.Axis(
                        labelAngle=0,
                        labelColor="#94A3B8",
                        titleColor="#38BDF8"
                    )
                ),
                y=alt.Y(
                    "mean(ai_score):Q",
                    title="Average Priority Rating",
                    axis=alt.Axis(
                        labelColor="#94A3B8",
                        titleColor="#38BDF8"
                    )
                ),
                color=alt.Color(
                    "department:N",
                    scale=alt.Scale(
                        domain=[
                            "Engineering",
                            "Electrical",
                            "S&T",
                            "Traffic"
                        ],
                        range=[
                            "#3B82F6",
                            "#10B981",
                            "#F59E0B",
                            "#EC4899"
                        ]
                    ),
                    legend=None
                ),
                tooltip=[
                    alt.Tooltip(
                        "department:N",
                        title="Department"
                    ),
                    alt.Tooltip(
                        "mean(ai_score):Q",
                        title="Average AI Score",
                        format=".1f"
                    )
                ]
            )
            .properties(height=280)
        )

        st.altair_chart(
            chart1,
            use_container_width=True
        )

    else:

        st.info(
            "No job data available for the chart."
        )


# ============================================================
# CHART 2 - STATUS DISTRIBUTION
# ============================================================

with v2:

    st.markdown(
        "#### Allocation Summary Status Profiles"
    )

    if not jobs_df.empty:

        chart2 = (
            alt.Chart(jobs_df)
            .mark_arc(
                innerRadius=60,
                stroke="#0F172A",
                strokeWidth=2
            )
            .encode(
                theta=alt.Theta(
                    field="job_id",
                    aggregate="count",
                    type="quantitative"
                ),
                color=alt.Color(
                    field="status",
                    type="nominal",
                    title="Status Plan",
                    scale=alt.Scale(
                        domain=[
                            "Pending",
                            "Scheduled",
                            "Delayed"
                        ],
                        range=[
                            "#EF4444",
                            "#10B981",
                            "#64748B"
                        ]
                    )
                ),
                tooltip=[
                    alt.Tooltip(
                        "status:N",
                        title="Status"
                    ),
                    alt.Tooltip(
                        "count(job_id):Q",
                        title="Jobs"
                    )
                ]
            )
            .properties(height=280)
        )

        st.altair_chart(
            chart2,
            use_container_width=True
        )

    else:

        st.info(
            "No job data available for the chart."
        )


# ============================================================
# SYSTEM DATA REGISTRIES
# ============================================================

st.markdown(
    "### 📋 System Data Registries",
    unsafe_allow_html=True
)

tab1, tab2 = st.tabs(
    [
        "🔒 Scheduled Shadow Blocks Mapping",
        "📋 Live Job Allocation Ledger"
    ]
)


# ============================================================
# TAB 1 - SCHEDULED BLOCKS
# ============================================================

with tab1:

    conn = get_db_connection()

    br_df = pd.read_sql_query(
        """
        SELECT
            block_id,
            track_id,
            window_start,
            window_end,
            total_departments_involved
        FROM Block_Register
        ORDER BY window_start DESC
        """,
        conn
    )

    conn.close()

    if not br_df.empty:

        st.dataframe(
            br_df,
            use_container_width=True
        )

    else:

        st.info(
            "Queue empty. Click "
            "'Run AI Auto-Scheduler' "
            "in the sidebar to populate live blocks!"
        )


# ============================================================
# TAB 2 - JOB ALLOCATION LEDGER
# ============================================================

with tab2:

    filter_status = st.radio(
        "Filter Ledger View:",
        [
            "All",
            "Pending",
            "Scheduled",
            "Delayed"
        ],
        horizontal=True
    )

    if filter_status == "All":

        display_df = jobs_df

    else:

        display_df = jobs_df[
            jobs_df["status"] == filter_status
        ]

    # Display selected columns only
    required_columns = [
        "job_id",
        "track_id",
        "department",
        "task",
        "defect_severity",
        "min_duration_needed",
        "ai_score",
        "status"
    ]

    available_columns = [
        col
        for col in required_columns
        if col in display_df.columns
    ]

    st.dataframe(
        display_df[available_columns],
        use_container_width=True
    )
