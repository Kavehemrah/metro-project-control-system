from pathlib import Path
import sqlite3

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "project.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS project (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    code TEXT,
    contract_value REAL DEFAULT 0,
    period_label TEXT,
    status TEXT DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS kpi (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    physical_progress REAL DEFAULT 0,
    revenue REAL DEFAULT 0,
    cost REAL DEFAULT 0,
    balance REAL DEFAULT 0,
    revenue_progress REAL DEFAULT 0,
    FOREIGN KEY(project_id) REFERENCES project(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS monthly_finance (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    month TEXT NOT NULL,
    revenue REAL DEFAULT 0,
    cost REAL DEFAULT 0,
    FOREIGN KEY(project_id) REFERENCES project(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    row_no INTEGER,
    position TEXT,
    zone TEXT,
    title TEXT,
    quantity REAL DEFAULT 0,
    remaining_qty REAL DEFAULT 0,
    unit TEXT,
    start_date TEXT,
    finish_date TEXT,
    duration_days INTEGER DEFAULT 0,
    daily_target REAL DEFAULT 0,
    planned_qty REAL DEFAULT 0,
    actual_qty REAL DEFAULT 0,
    progress REAL DEFAULT 0,
    delay_days INTEGER DEFAULT 0,
    status TEXT DEFAULT 'NORMAL',
    forecast_finish TEXT,
    notes TEXT,
    FOREIGN KEY(project_id) REFERENCES project(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS activity_daily_plan (
    id INTEGER PRIMARY KEY,
    activity_id INTEGER NOT NULL,
    plan_date TEXT NOT NULL,
    quantity REAL DEFAULT 0,
    source TEXT DEFAULT 'EXCEL',
    FOREIGN KEY(activity_id) REFERENCES activity(id) ON DELETE CASCADE,
    UNIQUE(activity_id, plan_date)
);

CREATE TABLE IF NOT EXISTS activity_daily_actual (
    id INTEGER PRIMARY KEY,
    activity_id INTEGER NOT NULL,
    actual_date TEXT NOT NULL,
    quantity REAL DEFAULT 0,
    source TEXT DEFAULT 'MANUAL',
    notes TEXT,
    FOREIGN KEY(activity_id) REFERENCES activity(id) ON DELETE CASCADE,
    UNIQUE(activity_id, actual_date)
);

CREATE TABLE IF NOT EXISTS activity_period (
    id INTEGER PRIMARY KEY,
    activity_id INTEGER NOT NULL,
    period TEXT NOT NULL,
    planned_qty REAL DEFAULT 0,
    actual_qty REAL DEFAULT 0,
    cumulative_qty REAL DEFAULT 0,
    remaining_qty REAL DEFAULT 0,
    achievement_pct REAL DEFAULT 0,
    source TEXT DEFAULT 'EXCEL',
    FOREIGN KEY(activity_id) REFERENCES activity(id) ON DELETE CASCADE,
    UNIQUE(activity_id, period)
);

CREATE TABLE IF NOT EXISTS activity_dependency (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    predecessor_activity_id INTEGER NOT NULL,
    relation_type TEXT DEFAULT 'finish_to_start',
    lag_days INTEGER DEFAULT 0,
    notes TEXT,
    FOREIGN KEY(project_id) REFERENCES project(id) ON DELETE CASCADE,
    FOREIGN KEY(activity_id) REFERENCES activity(id) ON DELETE CASCADE,
    FOREIGN KEY(predecessor_activity_id) REFERENCES activity(id) ON DELETE CASCADE,
    UNIQUE(project_id, activity_id, predecessor_activity_id)
);

CREATE TABLE IF NOT EXISTS revenue_entry (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    activity_id INTEGER,
    period TEXT,
    category TEXT NOT NULL,
    amount REAL DEFAULT 0,
    source TEXT DEFAULT 'EXCEL',
    FOREIGN KEY(project_id) REFERENCES project(id) ON DELETE CASCADE,
    FOREIGN KEY(activity_id) REFERENCES activity(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS cost_entry (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    activity_id INTEGER,
    period TEXT,
    category TEXT NOT NULL,
    amount REAL DEFAULT 0,
    source TEXT DEFAULT 'EXCEL',
    FOREIGN KEY(project_id) REFERENCES project(id) ON DELETE CASCADE,
    FOREIGN KEY(activity_id) REFERENCES activity(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS resource (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    category TEXT,
    title TEXT,
    required REAL DEFAULT 0,
    available REAL DEFAULT 0,
    unit TEXT,
    FOREIGN KEY(project_id) REFERENCES project(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS risk (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    category TEXT,
    title TEXT,
    probability INTEGER,
    impact INTEGER,
    control INTEGER,
    score INTEGER,
    action TEXT,
    FOREIGN KEY(project_id) REFERENCES project(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS discrepancy (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    title TEXT,
    detail TEXT,
    severity TEXT,
    FOREIGN KEY(project_id) REFERENCES project(id) ON DELETE CASCADE
);
"""


def _add_missing_columns(conn):
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(activity)").fetchall()
    }
    additions = {
        "position": "TEXT",
        "remaining_qty": "REAL DEFAULT 0",
        "start_date": "TEXT",
        "finish_date": "TEXT",
        "duration_days": "INTEGER DEFAULT 0",
        "daily_target": "REAL DEFAULT 0",
        "planned_qty": "REAL DEFAULT 0",
        "actual_qty": "REAL DEFAULT 0",
        "forecast_finish": "TEXT",
        "notes": "TEXT",
    }
    for name, definition in additions.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE activity ADD COLUMN {name} {definition}")


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _add_missing_columns(conn)
    conn.commit()
    return conn


def seed():
    conn = connect()
    if conn.execute("SELECT COUNT(*) FROM project").fetchone()[0]:
        conn.close()
        return

    conn.execute(
        "INSERT INTO project VALUES (1,?,?,?,?,?)",
        (
            "پروژه روسازی مترو بهارستان-اصفهان",
            "MB-1405-01",
            2856172028562,
            "مهر و آبان 1405",
            "ACTIVE",
        ),
    )
    conn.execute(
        "INSERT INTO kpi VALUES (1,1,?,?,?,?,?)",
        (
            0.1426,
            679135434966.0166,
            433410000000,
            245725434966.0166,
            0.14962598867734503,
        ),
    )

    for month, revenue, cost in [
        ("مهر 1405", 324156844396.787, 227000000000),
        ("آبان 1405", 354978590569.2297, 206000000000),
    ]:
        conn.execute(
            "INSERT INTO monthly_finance(project_id,month,revenue,cost) VALUES(1,?,?,?)",
            (month, revenue, cost),
        )

    activities = [
        (1, 1, 1, "تونل 4", "خط شرقی", "بتن‌ریزی اسلب", 1380, 538.8, "m",
         "1405/07/01", "1405/08/20", 51, 34.2, 885.4, 820, 0.12, 6, "CRITICAL"),
        (2, 1, 2, "تونل 3", "خط غربی", "پخش ریل", 1000, 320.5, "m",
         "1405/07/01", "1405/08/20", 51, 20, 320, 288, 0.18, 4, "CRITICAL"),
        (3, 1, 3, "تونل 4", "خط شرقی", "آرماتوربندی", 900, 410.2, "m",
         "1405/07/05", "1405/08/20", 47, 18, 410.2, 360, 0.10, 3, "CRITICAL"),
        (4, 1, 4, "تونل 3", "خط غربی", "پخش ریل", 1200, 750, "m",
         "1405/07/01", "1405/08/20", 51, 23, 750, 690, 0.25, 0, "NORMAL"),
        (5, 1, 5, "تونل 4", "خط شرقی", "تنظیم ریل", 800, 620, "m",
         "1405/07/10", "1405/08/20", 42, 15, 620, 540, 0.15, 1, "WARNING"),
    ]
    conn.executemany(
        """
        INSERT INTO activity
        (id, project_id, row_no, position, zone, title, quantity, remaining_qty,
         unit, start_date, finish_date, duration_days, daily_target, planned_qty,
         actual_qty, progress, delay_days, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        activities,
    )

    resources = [
        ("ماشین‌آلات", "تراک میکسر", 1, 1, "دستگاه"),
        ("ماشین‌آلات", "جرثقیل 10 تن", 1, 1, "دستگاه"),
        ("ماشین‌آلات", "جرثقیل حمل ریل", 1, 1, "دستگاه"),
        ("ماشین‌آلات", "لودر", 1, 1, "دستگاه"),
        ("نیروی انسانی", "نیروی اجرایی", 86, 74, "نفر"),
        ("مصالح", "سیمان", 1361.22, 0, "تن"),
        ("مصالح", "ماسه", 3539.50, 0, "تن"),
    ]
    conn.executemany(
        "INSERT INTO resource(project_id,category,title,required,available,unit) VALUES(1,?,?,?,?,?)",
        resources,
    )

    risks = [
        ("مالی", "عدم تامین نقدینگی جهت پرداخت حقوق معوقه پرسنل", 5, 5, 5, 125, "تأمین نقدینگی"),
        ("مالی", "عدم تامین نقدینگی جهت پرداخت مطالبات پیمانکاران جزء", 5, 5, 5, 125, "تأمین نقدینگی"),
        ("مالی", "عدم تامین نقدینگی جهت پرداخت مطالبات ماشین آلات استیجاری", 5, 5, 5, 125, "تأمین نقدینگی"),
        ("مالی", "عدم تأمین مالی جهت تعمیرات و خرید تجهیزات ایمن", 4, 4, 4, 64, "تأمین نقدینگی"),
        ("برنامه‌ریزی", "عدم امکان جذب و ماندگاری نیروی انسانی متخصص", 3, 3, 3, 27, "برنامه‌ریزی جذب نیروی متخصص"),
    ]
    conn.executemany(
        "INSERT INTO risk(project_id,category,title,probability,impact,control,score,action) VALUES(1,?,?,?,?,?,?,?)",
        risks,
    )

    discrepancies = [
        ("مغایرت هزینه خلاصه و جزئیات", "هزینه خلاصه با جزئیات حدود 12.33 میلیارد ریال اختلاف دارد.", "HIGH"),
        ("فرمول معیوب Excel", "چند سلول دارای #REF! در شیت پیشرفت فیزیکی هستند.", "HIGH"),
        ("برنامه و پیشرفت", "برخی مقادیر برنامه عملیاتی و پیشرفت فیزیکی نیاز به تطبیق دارند.", "MEDIUM"),
    ]
    conn.executemany(
        "INSERT INTO discrepancy(project_id,title,detail,severity) VALUES(1,?,?,?)",
        discrepancies,
    )

    conn.commit()
    conn.close()
