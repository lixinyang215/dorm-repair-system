"""宿舍报修管理系统 - 数据库模块"""
import sqlite3
import os
from datetime import datetime
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dorm_repair.db')


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.executescript('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        name TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('student','dorm_manager','admin')),
        building TEXT,
        phone TEXT,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS buildings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL
    );

    CREATE TABLE IF NOT EXISTS fault_types (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL
    );

    CREATE TABLE IF NOT EXISTS work_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        work_order_no TEXT UNIQUE NOT NULL,
        reporter_id INTEGER NOT NULL,
        reporter_name TEXT NOT NULL,
        location TEXT NOT NULL,
        building TEXT NOT NULL,
        fault_type TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        handler_id INTEGER,
        handler_name TEXT,
        repair_content TEXT,
        parts_replaced TEXT,
        duration_min INTEGER,
        created_at TEXT NOT NULL,
        accepted_at TEXT,
        repair_started_at TEXT,
        completed_at TEXT,
        confirmed_at TEXT,
        cancelled_at TEXT,
        FOREIGN KEY (reporter_id) REFERENCES users(id),
        FOREIGN KEY (handler_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS order_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        work_order_id INTEGER NOT NULL,
        image_path TEXT NOT NULL,
        image_type TEXT NOT NULL CHECK(image_type IN ('before','after')),
        FOREIGN KEY (work_order_id) REFERENCES work_orders(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        work_order_id INTEGER NOT NULL,
        rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
        comment TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (work_order_id) REFERENCES work_orders(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS operation_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        action TEXT NOT NULL,
        detail TEXT,
        created_at TEXT NOT NULL
    );
    ''')

    conn.commit()

    # 种子数据
    if c.execute("SELECT COUNT(*) FROM buildings").fetchone()[0] == 0:
        for b in ['1栋', '2栋', '3栋', '4栋', '5栋']:
            c.execute("INSERT INTO buildings(name) VALUES (?)", (b,))

    if c.execute("SELECT COUNT(*) FROM fault_types").fetchone()[0] == 0:
        for f in ['水电', '家具', '门窗', '网络', '空调', '其他']:
            c.execute("INSERT INTO fault_types(name) VALUES (?)", (f,))

    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        seed_users = [
            ('admin', 'admin123', '系统管理员', 'admin', None, '13800000000', now),
            ('manager1', 'm123', '张师傅', 'dorm_manager', '3栋', '13800000001', now),
            ('manager2', 'm123', '李师傅', 'dorm_manager', '4栋', '13800000002', now),
            ('stu001', 's123', '王小明', 'student', '3栋', '13800000003', now),
            ('stu002', 's123', '赵小红', 'student', '3栋', '13800000004', now),
            ('stu003', 's123', '孙小刚', 'student', '4栋', '13800000005', now),
        ]
        for u in seed_users:
            c.execute(
                "INSERT INTO users(username,password_hash,name,role,building,phone,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (u[0], generate_password_hash(u[1]), u[2], u[3], u[4], u[5], 'active', u[6])
            )

    conn.commit()
    conn.close()


def now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
