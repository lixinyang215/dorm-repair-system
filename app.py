"""宿舍报修管理系统 - Flask 主应用"""
import os
import io
import uuid
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, send_from_directory, send_file, abort)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from PIL import Image
import openpyxl

from db import get_conn, init_db, now_str

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dorm-repair-secret-key-2026'
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
FAULT_TYPES = ['水电', '家具', '门窗', '网络', '空调', '其他']

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


@app.template_filter('zip')
def _zip(a, b):
    return zip(a, b)


# ----------------- 工具函数 -----------------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


def save_images(files, work_order_id, image_type):
    """保存图片并返回路径列表"""
    paths = []
    conn = get_conn()
    for f in files:
        if f and f.filename and allowed_file(f.filename):
            ext = f.filename.rsplit('.', 1)[1].lower()
            fname = f"{uuid.uuid4().hex}.{ext}"
            fpath = os.path.join(app.config['UPLOAD_FOLDER'], fname)
            # 验证图片并压缩
            try:
                img = Image.open(f)
                img.thumbnail((1280, 1280))
                img.save(fpath, optimize=True)
            except Exception:
                f.save(fpath)
            conn.execute(
                "INSERT INTO order_images(work_order_id,image_path,image_type) VALUES (?,?,?)",
                (work_order_id, fname, image_type)
            )
            paths.append(fname)
    conn.commit()
    conn.close()
    return paths


def get_order_images(work_order_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT image_path,image_type FROM order_images WHERE work_order_id=? ORDER BY id",
        (work_order_id,)
    ).fetchall()
    conn.close()
    before = [r['image_path'] for r in rows if r['image_type'] == 'before']
    after = [r['image_path'] for r in rows if r['image_type'] == 'after']
    return before, after


def gen_order_no():
    conn = get_conn()
    today = datetime.now().strftime('%Y%m%d')
    row = conn.execute(
        "SELECT COUNT(*) as c FROM work_orders WHERE work_order_no LIKE ?",
        (f'WO{today}%',)
    ).fetchone()
    conn.close()
    return f"WO{today}{row['c'] + 1:03d}"


def log_action(user_id, username, action, detail=''):
    conn = get_conn()
    conn.execute(
        "INSERT INTO operation_logs(user_id,username,action,detail,created_at) VALUES (?,?,?,?,?)",
        (user_id, username, action, detail, now_str())
    )
    conn.commit()
    conn.close()


# ----------------- 登录鉴权装饰器 -----------------
def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrap


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrap(*args, **kwargs):
            if session.get('role') not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrap
    return decorator


# ----------------- 认证路由 -----------------
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    role = session['role']
    if role == 'student':
        return redirect(url_for('student_orders'))
    elif role == 'dorm_manager':
        return redirect(url_for('manager_pending'))
    else:
        return redirect(url_for('admin_dashboard'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        conn = get_conn()
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password_hash'], password):
            if user['status'] != 'active':
                flash('账号已被禁用，请联系管理员', 'error')
                return render_template('login.html')
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['name'] = user['name']
            session['role'] = user['role']
            session['building'] = user['building'] or ''
            log_action(user['id'], user['username'], '登录', f"{user['name']} 登录系统")
            return redirect(url_for('index'))
        flash('用户名或密码错误', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ----------------- 学生端路由 -----------------
@app.route('/student/orders')
@login_required
@role_required('student')
def student_orders():
    status_filter = request.args.get('status', '')
    conn = get_conn()
    sql = "SELECT * FROM work_orders WHERE reporter_id=? "
    params = [session['user_id']]
    if status_filter:
        sql += "AND status=? "
        params.append(status_filter)
    sql += "ORDER BY created_at DESC"
    orders = conn.execute(sql, params).fetchall()
    conn.close()
    return render_template('student/orders.html', orders=orders, status_filter=status_filter)


@app.route('/student/orders/new', methods=['GET', 'POST'])
@login_required
@role_required('student')
def student_new_order():
    if request.method == 'POST':
        location = request.form.get('location', '').strip()
        fault_type = request.form.get('fault_type', '').strip()
        description = request.form.get('description', '').strip()
        building = request.form.get('building', session.get('building', '')).strip()
        if not all([location, fault_type, description, building]):
            flash('请填写完整信息', 'error')
            return render_template('student/new_order.html', fault_types=FAULT_TYPES)
        conn = get_conn()
        order_no = gen_order_no()
        cur = conn.execute(
            """INSERT INTO work_orders
               (work_order_no,reporter_id,reporter_name,location,building,fault_type,description,status,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (order_no, session['user_id'], session['name'], location, building,
             fault_type, description, 'pending', now_str())
        )
        work_order_id = cur.lastrowid
        conn.commit()
        conn.close()
        # 保存图片
        images = request.files.getlist('images')
        save_images(images, work_order_id, 'before')
        log_action(session['user_id'], session['username'], '提交工单', f"工单 {order_no}")
        flash('报修提交成功！', 'success')
        return redirect(url_for('student_order_detail', order_id=work_order_id))
    return render_template('student/new_order.html', fault_types=FAULT_TYPES)


@app.route('/student/orders/<int:order_id>')
@login_required
@role_required('student')
def student_order_detail(order_id):
    conn = get_conn()
    order = conn.execute("SELECT * FROM work_orders WHERE id=? AND reporter_id=?",
                         (order_id, session['user_id'])).fetchone()
    if not order:
        conn.close()
        abort(404)
    eval_row = conn.execute("SELECT * FROM evaluations WHERE work_order_id=?", (order_id,)).fetchone()
    conn.close()
    before_imgs, after_imgs = get_order_images(order_id)
    return render_template('student/order_detail.html', order=order,
                           before_imgs=before_imgs, after_imgs=after_imgs, evaluation=eval_row)


@app.route('/student/orders/<int:order_id>/cancel', methods=['POST'])
@login_required
@role_required('student')
def student_cancel_order(order_id):
    conn = get_conn()
    order = conn.execute("SELECT * FROM work_orders WHERE id=? AND reporter_id=?",
                         (order_id, session['user_id'])).fetchone()
    if not order or order['status'] != 'pending':
        conn.close()
        flash('该工单无法取消', 'error')
        return redirect(url_for('student_orders'))
    conn.execute("UPDATE work_orders SET status='cancelled', cancelled_at=? WHERE id=?",
                 (now_str(), order_id))
    conn.commit()
    conn.close()
    log_action(session['user_id'], session['username'], '取消工单', order['work_order_no'])
    flash('工单已取消', 'success')
    return redirect(url_for('student_orders'))


@app.route('/student/orders/<int:order_id>/confirm', methods=['POST'])
@login_required
@role_required('student')
def student_confirm_order(order_id):
    conn = get_conn()
    order = conn.execute("SELECT * FROM work_orders WHERE id=? AND reporter_id=?",
                         (order_id, session['user_id'])).fetchone()
    if not order or order['status'] != 'waiting_confirm':
        conn.close()
        flash('该工单无法验收', 'error')
        return redirect(url_for('student_orders'))
    conn.execute("UPDATE work_orders SET status='completed', confirmed_at=? WHERE id=?",
                 (now_str(), order_id))
    conn.commit()
    conn.close()
    log_action(session['user_id'], session['username'], '验收确认', order['work_order_no'])
    flash('已确认验收，工单完成', 'success')
    return redirect(url_for('student_order_detail', order_id=order_id))


@app.route('/student/orders/<int:order_id>/evaluate', methods=['POST'])
@login_required
@role_required('student')
def student_evaluate(order_id):
    rating = int(request.form.get('rating', 0))
    comment = request.form.get('comment', '').strip()
    conn = get_conn()
    order = conn.execute("SELECT * FROM work_orders WHERE id=? AND reporter_id=?",
                         (order_id, session['user_id'])).fetchone()
    if not order or order['status'] != 'completed':
        conn.close()
        flash('无法评价', 'error')
        return redirect(url_for('student_orders'))
    exists = conn.execute("SELECT id FROM evaluations WHERE work_order_id=?", (order_id,)).fetchone()
    if exists:
        conn.execute("UPDATE evaluations SET rating=?, comment=? WHERE work_order_id=?",
                     (rating, comment, order_id))
    else:
        conn.execute("INSERT INTO evaluations(work_order_id,rating,comment,created_at) VALUES (?,?,?,?)",
                     (order_id, rating, comment, now_str()))
    conn.commit()
    conn.close()
    flash('评价已提交', 'success')
    return redirect(url_for('student_order_detail', order_id=order_id))


# ----------------- 宿管端路由 -----------------
@app.route('/manager/pending')
@login_required
@role_required('dorm_manager')
def manager_pending():
    conn = get_conn()
    orders = conn.execute(
        "SELECT * FROM work_orders WHERE status='pending' AND building=? ORDER BY created_at ASC",
        (session['building'],)
    ).fetchall()
    conn.close()
    return render_template('manager/pending.html', orders=orders)


@app.route('/manager/accepted')
@login_required
@role_required('dorm_manager')
def manager_accepted():
    conn = get_conn()
    orders = conn.execute(
        "SELECT * FROM work_orders WHERE handler_id=? AND status IN ('accepted','repairing') ORDER BY created_at ASC",
        (session['user_id'],)
    ).fetchall()
    conn.close()
    return render_template('manager/accepted.html', orders=orders)


@app.route('/manager/orders/<int:order_id>/accept', methods=['POST'])
@login_required
@role_required('dorm_manager')
def manager_accept(order_id):
    conn = get_conn()
    order = conn.execute("SELECT * FROM work_orders WHERE id=?", (order_id,)).fetchone()
    if not order or order['status'] != 'pending' or order['building'] != session['building']:
        conn.close()
        flash('无法接单', 'error')
        return redirect(url_for('manager_pending'))
    conn.execute(
        "UPDATE work_orders SET status='accepted', handler_id=?, handler_name=?, accepted_at=? WHERE id=?",
        (session['user_id'], session['name'], now_str(), order_id)
    )
    conn.commit()
    conn.close()
    log_action(session['user_id'], session['username'], '接单', order['work_order_no'])
    flash('接单成功', 'success')
    return redirect(url_for('manager_accepted'))


@app.route('/manager/orders/<int:order_id>/start-repair', methods=['POST'])
@login_required
@role_required('dorm_manager')
def manager_start_repair(order_id):
    conn = get_conn()
    order = conn.execute("SELECT * FROM work_orders WHERE id=? AND handler_id=?",
                         (order_id, session['user_id'])).fetchone()
    if not order or order['status'] != 'accepted':
        conn.close()
        flash('无法开始维修', 'error')
        return redirect(url_for('manager_accepted'))
    conn.execute("UPDATE work_orders SET status='repairing', repair_started_at=? WHERE id=?",
                 (now_str(), order_id))
    conn.commit()
    conn.close()
    log_action(session['user_id'], session['username'], '开始维修', order['work_order_no'])
    return redirect(url_for('manager_repair_record', order_id=order_id))


@app.route('/manager/orders/<int:order_id>/repair-record', methods=['GET', 'POST'])
@login_required
@role_required('dorm_manager')
def manager_repair_record(order_id):
    conn = get_conn()
    order = conn.execute("SELECT * FROM work_orders WHERE id=? AND handler_id=?",
                         (order_id, session['user_id'])).fetchone()
    if not order:
        conn.close()
        abort(404)
    if request.method == 'POST':
        content = request.form.get('repair_content', '').strip()
        parts = request.form.get('parts_replaced', '').strip()
        duration = int(request.form.get('duration_min', 0))
        if not content or duration <= 0:
            flash('请填写维修内容和用时', 'error')
            conn.close()
            return redirect(url_for('manager_repair_record', order_id=order_id))
        conn.execute(
            """UPDATE work_orders SET repair_content=?, parts_replaced=?, duration_min=?,
               status='waiting_confirm', completed_at=? WHERE id=?""",
            (content, parts, duration, now_str(), order_id)
        )
        conn.commit()
        conn.close()
        images = request.files.getlist('after_images')
        save_images(images, order_id, 'after')
        log_action(session['user_id'], session['username'], '完工', order['work_order_no'])
        flash('维修记录已提交，等待学生验收', 'success')
        return redirect(url_for('manager_accepted'))
    before_imgs, after_imgs = get_order_images(order_id)
    conn.close()
    return render_template('manager/repair_record.html', order=order,
                           before_imgs=before_imgs, after_imgs=after_imgs)


@app.route('/manager/history')
@login_required
@role_required('dorm_manager')
def manager_history():
    conn = get_conn()
    orders = conn.execute(
        "SELECT * FROM work_orders WHERE handler_id=? AND status='completed' ORDER BY completed_at DESC",
        (session['user_id'],)
    ).fetchall()
    conn.close()
    return render_template('manager/history.html', orders=orders)


# ----------------- 管理员端路由 -----------------
@app.route('/admin/dashboard')
@login_required
@role_required('admin')
def admin_dashboard():
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) c FROM work_orders").fetchone()['c']
    pending = conn.execute("SELECT COUNT(*) c FROM work_orders WHERE status='pending'").fetchone()['c']
    processing = conn.execute(
        "SELECT COUNT(*) c FROM work_orders WHERE status IN ('accepted','repairing')"
    ).fetchone()['c']
    completed = conn.execute("SELECT COUNT(*) c FROM work_orders WHERE status='completed'").fetchone()['c']
    # 故障分类统计
    fault_stats = conn.execute(
        "SELECT fault_type, COUNT(*) c FROM work_orders GROUP BY fault_type ORDER BY c DESC"
    ).fetchall()
    # 状态分布
    status_stats = [dict(r) for r in conn.execute(
        "SELECT status, COUNT(*) c FROM work_orders GROUP BY status"
    ).fetchall()]
    # 近7天报修趋势
    rows = conn.execute(
        "SELECT DATE(created_at) d, COUNT(*) c FROM work_orders "
        "WHERE created_at >= date('now','-6 days') GROUP BY DATE(created_at) ORDER BY d"
    ).fetchall()
    # 平均维修时效（已完工单：completed_at - accepted_at）
    dur_row = conn.execute(
        "SELECT AVG(duration_min) avg_dur FROM work_orders WHERE duration_min IS NOT NULL"
    ).fetchone()
    avg_dur = round(dur_row['avg_dur'] or 0, 1)
    conn.close()

    trend_labels = [r['d'] for r in rows]
    trend_data = [r['c'] for r in rows]
    fault_labels = [r['fault_type'] for r in fault_stats]
    fault_data = [r['c'] for r in fault_stats]

    return render_template('admin/dashboard.html',
                           total=total, pending=pending, processing=processing, completed=completed,
                           avg_dur=avg_dur,
                           trend_labels=trend_labels, trend_data=trend_data,
                           fault_labels=fault_labels, fault_data=fault_data,
                           status_stats=status_stats)


@app.route('/admin/orders')
@login_required
@role_required('admin')
def admin_orders():
    status = request.args.get('status', '')
    building = request.args.get('building', '')
    fault_type = request.args.get('fault_type', '')
    conn = get_conn()
    sql = "SELECT * FROM work_orders WHERE 1=1"
    params = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if building:
        sql += " AND building=?"
        params.append(building)
    if fault_type:
        sql += " AND fault_type=?"
        params.append(fault_type)
    sql += " ORDER BY created_at DESC LIMIT 500"
    orders = conn.execute(sql, params).fetchall()
    buildings = [r['name'] for r in conn.execute("SELECT name FROM buildings ORDER BY name").fetchall()]
    conn.close()
    return render_template('admin/orders.html', orders=orders, buildings=buildings,
                           fault_types=FAULT_TYPES, status=status, building=building, fault_type=fault_type)


@app.route('/admin/orders/export')
@login_required
@role_required('admin')
def admin_export_orders():
    status = request.args.get('status', '')
    building = request.args.get('building', '')
    fault_type = request.args.get('fault_type', '')
    conn = get_conn()
    sql = "SELECT * FROM work_orders WHERE 1=1"
    params = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if building:
        sql += " AND building=?"
        params.append(building)
    if fault_type:
        sql += " AND fault_type=?"
        params.append(fault_type)
    sql += " ORDER BY created_at DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '报修记录'
    headers = ['工单号', '报修人', '故障位置', '楼栋', '故障类型', '故障描述', '状态',
               '处理人', '维修内容', '更换配件', '维修用时(分)', '提交时间', '接单时间', '完工时间', '确认时间']
    ws.append(headers)
    status_map = {'pending': '待接单', 'accepted': '已接单', 'repairing': '维修中',
                  'waiting_confirm': '待验收', 'completed': '已完成', 'cancelled': '已取消'}
    for r in rows:
        ws.append([
            r['work_order_no'], r['reporter_name'], r['location'], r['building'], r['fault_type'],
            r['description'], status_map.get(r['status'], r['status']),
            r['handler_name'] or '', r['repair_content'] or '', r['parts_replaced'] or '',
            r['duration_min'] or '', r['created_at'], r['accepted_at'] or '',
            r['completed_at'] or '', r['confirmed_at'] or ''
        ])
    # 列宽
    for col in ws.columns:
        max_len = max(len(str(c.value or '')) for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    log_action(session['user_id'], session['username'], '导出Excel', f'导出 {len(rows)} 条记录')
    return send_file(buf, as_attachment=True,
                     download_name=f'报修记录_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.route('/admin/users')
@login_required
@role_required('admin')
def admin_users():
    role = request.args.get('role', 'student')
    conn = get_conn()
    users = conn.execute("SELECT * FROM users WHERE role=? ORDER BY id", (role,)).fetchall()
    buildings = [r['name'] for r in conn.execute("SELECT name FROM buildings ORDER BY name").fetchall()]
    conn.close()
    return render_template('admin/users.html', users=users, role=role, buildings=buildings)


@app.route('/admin/users/create', methods=['POST'])
@login_required
@role_required('admin')
def admin_user_create():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    name = request.form.get('name', '').strip()
    role = request.form.get('role', 'student').strip()
    building = request.form.get('building', '').strip() or None
    phone = request.form.get('phone', '').strip()
    if not all([username, password, name]):
        flash('请填写完整信息', 'error')
        return redirect(url_for('admin_users', role=role))
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO users(username,password_hash,name,role,building,phone,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (username, generate_password_hash(password), name, role, building, phone, 'active', now_str())
        )
        conn.commit()
        flash('账号创建成功', 'success')
    except sqlite3.IntegrityError:
        flash('用户名已存在', 'error')
    conn.close()
    return redirect(url_for('admin_users', role=role))


@app.route('/admin/users/<int:user_id>/toggle', methods=['POST'])
@login_required
@role_required('admin')
def admin_user_toggle(user_id):
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user:
        new_status = 'disabled' if user['status'] == 'active' else 'active'
        conn.execute("UPDATE users SET status=? WHERE id=?", (new_status, user_id))
        conn.commit()
    conn.close()
    return redirect(url_for('admin_users', role=user['role']))


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_user_delete(user_id):
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user and user['id'] != session['user_id']:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
        flash('账号已删除', 'success')
    conn.close()
    return redirect(url_for('admin_users', role=user['role'] if user else 'student'))


@app.route('/admin/users/<int:user_id>/reset-password', methods=['POST'])
@login_required
@role_required('admin')
def admin_user_reset_password(user_id):
    new_pwd = request.form.get('password', '123456').strip()
    conn = get_conn()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (generate_password_hash(new_pwd), user_id))
    conn.commit()
    conn.close()
    flash('密码已重置', 'success')
    return redirect(url_for('admin_users', role='student'))


@app.route('/admin/config')
@login_required
@role_required('admin')
def admin_config():
    conn = get_conn()
    buildings = conn.execute("SELECT * FROM buildings ORDER BY id").fetchall()
    fault_types = conn.execute("SELECT * FROM fault_types ORDER BY id").fetchall()
    # 楼栋-宿管映射
    managers = conn.execute(
        "SELECT id,name,building FROM users WHERE role='dorm_manager' ORDER BY id"
    ).fetchall()
    conn.close()
    return render_template('admin/config.html', buildings=buildings,
                           fault_types=fault_types, managers=managers)


@app.route('/admin/config/building/add', methods=['POST'])
@login_required
@role_required('admin')
def admin_add_building():
    name = request.form.get('name', '').strip()
    if name:
        conn = get_conn()
        try:
            conn.execute("INSERT INTO buildings(name) VALUES (?)", (name,))
            conn.commit()
        except sqlite3.IntegrityError:
            flash('楼栋已存在', 'error')
        conn.close()
    return redirect(url_for('admin_config'))


@app.route('/admin/config/fault-type/add', methods=['POST'])
@login_required
@role_required('admin')
def admin_add_fault_type():
    name = request.form.get('name', '').strip()
    if name:
        conn = get_conn()
        try:
            conn.execute("INSERT INTO fault_types(name) VALUES (?)", (name,))
            conn.commit()
        except sqlite3.IntegrityError:
            flash('故障类型已存在', 'error')
        conn.close()
    return redirect(url_for('admin_config'))


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


if __name__ == '__main__':
    init_db()
    print("=" * 50)
    print("宿舍报修管理系统已启动")
    print("访问地址: http://127.0.0.1:5000")
    print("测试账号: admin/admin123  manager1/m123  stu001/s123")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=True)
