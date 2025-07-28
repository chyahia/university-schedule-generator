# Copyright (C) 2025 CHAIB YAHIA
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by

# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

# ================== الجزء الأول: استيراد المكتبات ==================
from flask import Flask, jsonify, request, render_template, send_file, g
import os
import json
import pandas as pd
import io
import threading
import signal
import webbrowser
import sys
import random
import sqlite3 # <-- المكتبة الجديدة لقاعدة البيانات
import copy
from waitress import serve
import time
import queue
from concurrent.futures import ThreadPoolExecutor
from flask import stream_with_context, Response
import math
import traceback
from collections import deque
from collections import defaultdict, Counter

# ================== الجزء الثاني: إعداد قاعدة البيانات والمسارات ==================
def get_base_path():
    """
    يحدد المسار الأساسي للتطبيق سواء كان يعمل كملف تنفيذي أو كسكربت بايثون.
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_path()
# تحديد مسار ملف قاعدة البيانات
DATABASE_FILE = os.path.join(BASE_DIR, 'schedule_database.db')

def get_db_connection():
    """
    تنشئ اتصالًا جديدًا بقاعدة البيانات إذا لم يكن هناك واحد بالفعل لهذا الطلب.
    """
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE_FILE)
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.row_factory = sqlite3.Row
    return g.db


    

# ================== الجزء الرابع: واجهات الويب (API Endpoints) ==================
app = Flask(__name__)
log_queue = queue.Queue()
executor = ThreadPoolExecutor(max_workers=1)
SCHEDULING_STATE = {'should_stop': False}
class StopByUserException(Exception):
    """Exception raised to stop the algorithm cleanly."""
    pass

@app.teardown_appcontext
def close_db(e=None):
    """تغلق اتصال قاعدة البيانات عند انتهاء معالجة الطلب."""
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    """
    تنشئ الجداول في قاعدة البيانات إذا لم تكن موجودة.
    يتم استدعاء هذه الدالة مرة واحدة عند بدء تشغيل التطبيق.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # جدول المستويات الدراسية (لا تغيير)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS levels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE
    )''')

    # جدول الأساتذة (لا تغيير)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS teachers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE
    )''')

    # جدول القاعات (لا تغيير)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS rooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        type TEXT NOT NULL
    )''')
    
    # جدول المقررات (تم تعديله: حذف level_id)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        room_type TEXT NOT NULL,
        teacher_id INTEGER,
        FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE SET NULL
    )''')

    # === ✨ بداية الإضافة الجديدة: جدول الربط بين المقررات والمستويات ===
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS course_levels (
        course_id INTEGER,
        level_id INTEGER,
        PRIMARY KEY (course_id, level_id),
        FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
        FOREIGN KEY (level_id) REFERENCES levels (id) ON DELETE CASCADE
    )''')
    # === نهاية الإضافة الجديدة ===

    # جدول الإعدادات (لا تغيير)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )''')

    conn.commit()
    conn.close()

# ================== الجزء الثالث: دوال مساعدة للتعامل مع قاعدة البيانات ==================

def query_db(query, args=(), one=False):
    """
    دالة عامة لتنفيذ استعلامات SELECT.
    """
    conn = get_db_connection()
    cur = conn.execute(query, args)
    rv = [dict(row) for row in cur.fetchall()]
    
    return (rv[0] if rv else None) if one else rv

def execute_db(query, args=()):
    """
    دالة عامة لتنفيذ استعلامات INSERT, UPDATE, DELETE.
    """
    conn = get_db_connection()
    conn.execute(query, args)
    conn.commit()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/stop-generation', methods=['POST'])
def stop_generation_endpoint():
    if 'should_stop' in SCHEDULING_STATE:
        SCHEDULING_STATE['should_stop'] = True
        log_queue.put('\n--- تم استلام طلب الإيقاف، سيتم إنهاء البحث قريباً... ---')
    return jsonify({"success": True, "message": "تم إرسال إشارة الإيقاف."})
        
# --- واجهات جلب البيانات الأولية (محولة إلى SQLite) ---
@app.route('/students', methods=['GET'])
def get_courses():
    # استعلام معقد يجمع البيانات من عدة جداول ويعيدها بشكل متوافق
    courses_query = '''
        SELECT 
            c.id, 
            c.name, 
            c.room_type, 
            t.name as teacher_name,
            GROUP_CONCAT(l.name) as levels -- ✨ تجميع كل المستويات في نص واحد
        FROM courses c
        LEFT JOIN teachers t ON c.teacher_id = t.id
        LEFT JOIN course_levels cl ON c.id = cl.course_id
        LEFT JOIN levels l ON cl.level_id = l.id
        GROUP BY c.id, c.name, c.room_type, t.name
        ORDER BY c.name
    '''
    courses_data = query_db(courses_query)
    
    # ✨ تحويل النص المجمع إلى قائمة (list) في بايثون
    for course in courses_data:
        if course.get('levels'):
            # نستخدم فاصلة بدون مسافة هنا لتتوافق مع GROUP_CONCAT
            course['levels'] = [level.strip() for level in course['levels'].split(',')]
        else:
            course['levels'] = []
            
    return jsonify(courses_data)

@app.route('/teachers', methods=['GET'])
def get_teachers():
    return jsonify(query_db('SELECT id, name FROM teachers'))

@app.route('/rooms', methods=['GET'])
def get_rooms():
    return jsonify(query_db('SELECT id, name, type FROM rooms'))

@app.route('/api/levels', methods=['GET'])
def get_levels():
    # الواجهة الأمامية تتوقع قائمة من الأسماء فقط
    levels_data = query_db('SELECT name FROM levels ORDER BY name')
    return jsonify([level['name'] for level in levels_data])

@app.route('/api/levels', methods=['POST'])
def add_levels():
    new_levels = request.json.get('levels', [])
    if not new_levels:
        return jsonify({"error": "قائمة المستويات فارغة"}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    added_count = 0
    for level_name in new_levels:
        # IGNORE تتجاهل الإضافة إذا كان المستوى موجودًا بالفعل
        cursor.execute('INSERT OR IGNORE INTO levels (name) VALUES (?)', (level_name,))
        if cursor.rowcount > 0:
            added_count += 1
    conn.commit()
    conn.close()
    
    return jsonify({"success": True, "message": f"تمت إضافة {added_count} مستويات."}), 201

# --- واجهة حفظ وتحميل الإعدادات (محولة إلى SQLite) ---
@app.route('/api/settings', methods=['GET', 'POST'])
def handle_settings():
    if request.method == 'POST':
        settings_data = request.get_json()
        # تخزين كائن الإعدادات كـنص JSON في قاعدة البيانات
        settings_json = json.dumps(settings_data, ensure_ascii=False)
        execute_db('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', ('main_settings', settings_json))
        return jsonify({"success": True, "message": "تم حفظ الإعدادات بنجاح."})
    
    # GET request
    settings_row = query_db('SELECT value FROM settings WHERE key = ?', ('main_settings',), one=True)
    if settings_row:
        # استرجاع نص JSON وتحويله إلى كائن بايثون
        return jsonify(json.loads(settings_row['value']))
    return jsonify({}) # إرجاع كائن فارغ إذا لم توجد إعدادات

@app.route('/api/settings/save_as', methods=['POST'])
def save_settings_as():
    data = request.get_json()
    settings_name = data.get('name')
    settings_data = data.get('settings')

    if not settings_name or not settings_data:
        return jsonify({"error": "الاسم أو الإعدادات مفقودة."}), 400

    settings_json = json.dumps(settings_data, ensure_ascii=False)
    # استخدام اسم الإعدادات كمفتاح
    execute_db('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (f'named_settings_{settings_name}', settings_json))
    return jsonify({"success": True, "message": f"تم حفظ الإعدادات باسم '{settings_name}' بنجاح."})

@app.route('/api/settings/load_named', methods=['GET'])
def load_named_settings():
    settings_name = request.args.get('name') # نستخدم request.args.get لـ GET requests
    if not settings_name:
        return jsonify({"error": "اسم الإعدادات مفقود."}), 400

    settings_row = query_db('SELECT value FROM settings WHERE key = ?', (f'named_settings_{settings_name}',), one=True)
    if settings_row:
        return jsonify(json.loads(settings_row['value']))
    return jsonify({"error": "لم يتم العثور على الإعدادات بهذا الاسم."}), 404

@app.route('/api/settings/get_saved_names', methods=['GET'])
def get_saved_settings_names():
    # جلب جميع المفاتيح التي تبدأ بـ 'named_settings_'
    saved_names = query_db("SELECT key FROM settings WHERE key LIKE 'named_settings_%'")
    # استخلاص الاسم الحقيقي من المفتاح (إزالة 'named_settings_')
    names_list = [name['key'].replace('named_settings_', '') for name in saved_names]
    return jsonify(names_list)

@app.route('/api/settings/delete_named', methods=['DELETE'])
def delete_named_settings():
    data = request.get_json()
    settings_name = data.get('name')
    if not settings_name:
        return jsonify({"error": "اسم الإعدادات مفقود للحذف."}), 400
    
    execute_db('DELETE FROM settings WHERE key = ?', (f'named_settings_{settings_name}',))
    return jsonify({"success": True, "message": f"تم حذف الإعدادات باسم '{settings_name}' بنجاح."})

@app.route('/api/identifiers', methods=['GET', 'POST'])
def handle_identifiers():
    if request.method == 'POST':
        # حفظ المعرفات الجديدة القادمة من الواجهة
        identifiers_data = request.get_json()
        identifiers_json = json.dumps(identifiers_data, ensure_ascii=False)
        execute_db('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', ('non_repetition_identifiers', identifiers_json))
        return jsonify({"success": True, "message": "تم حفظ المعرّفات بنجاح."})
    
    # GET request
    # استرجاع المعرفات المحفوظة
    result = query_db('SELECT value FROM settings WHERE key = ?', ('non_repetition_identifiers',), one=True)
    if result and result['value']:
        return jsonify(json.loads(result['value']))
    return jsonify({}) # إرجاع كائن فارغ إذا لم توجد معرفات

# ================== خوارزمية الجدولة الرئيسية (تبقى كما هي مع تعديل مصدر البيانات) ==================
def process_schedule_structure(structure):
    if not structure: return [], [], {}, {}, []
    days = list(structure.keys())
    all_time_keys = set()
    for day_slots in structure.values():
        all_time_keys.update(day_slots.keys())
    slots = sorted(list(all_time_keys))
    day_to_idx = {day: i for i, day in enumerate(days)}
    slot_to_idx = {slot: i for i, slot in enumerate(slots)}
    rules_grid = [[[] for _ in slots] for _ in days]
    for day_name, day_slots in structure.items():
        day_idx = day_to_idx.get(day_name)
        if day_idx is None: continue
        # === بداية التعديل: التعامل مع كائن الفترة الزمنية بدلاً من مصفوفة ===
        for time_key, slot_info in day_slots.items():
            slot_idx = slot_to_idx.get(time_key)
            if slot_idx is None: continue
            # نستخرج القواعد من الكائن
            rules_grid[day_idx][slot_idx] = slot_info.get('rules', [])
        # === نهاية التعديل ===
    return days, slots, day_to_idx, slot_to_idx, rules_grid

class TimeoutException(Exception):
    pass

def get_contained_identifier(course_name, identifiers_for_level):
    """تبحث عن أول معرّف من القائمة موجود داخل اسم المادة"""
    if not identifiers_for_level:
        return None
    for identifier in identifiers_for_level:
        if identifier in course_name:
            return identifier
    return None



def _find_best_greedy_placement_in_slots(slots_to_search, lecture, final_schedule, teacher_schedule, teacher_constraints, special_constraints, identifiers_by_level, rules_grid, globally_unavailable_slots, room_schedule, rooms_data, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule):
    best_placement = None
    max_fitness = -1

    for day_idx, slot_idx in slots_to_search:
        # تمرير المعلومات الجديدة للدالة النهائية
        is_valid, result_or_reason = is_placement_valid(
            lecture, day_idx, slot_idx, final_schedule, teacher_schedule,
            room_schedule, teacher_constraints, special_constraints,
            identifiers_by_level, rules_grid, globally_unavailable_slots, rooms_data,
            saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule
        )
        if not is_valid: continue
        
        available_room = result_or_reason
        current_fitness = calculate_slot_fitness(lecture.get('teacher_name'), day_idx, slot_idx, teacher_schedule, special_constraints)

        if current_fitness > max_fitness:
            max_fitness = current_fitness
            best_placement = {"day_idx": day_idx, "slot_idx": slot_idx, "room": available_room}
    return best_placement

def find_slot_for_single_lecture(lecture, final_schedule, teacher_schedule, room_schedule,
                                 days, slots, rules_grid, rooms_data,
                                 teacher_constraints, globally_unavailable_slots, special_constraints,
                                 primary_slots, reserve_slots, identifiers_by_level, prioritize_primary,
                                 saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule):
    teacher = lecture.get('teacher_name')
    if not teacher: 
        return False, "المادة غير مسندة لأستاذ"
    
    best_placement = None
    is_large_room_course = lecture.get('room_type') == 'كبيرة'
    
    args_for_placement = (lecture, final_schedule, teacher_schedule, teacher_constraints, special_constraints, identifiers_by_level, rules_grid, globally_unavailable_slots, room_schedule, rooms_data, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule)

    if is_large_room_course and prioritize_primary:
        best_placement = _find_best_greedy_placement_in_slots(primary_slots, *args_for_placement)
        if not best_placement:
            best_placement = _find_best_greedy_placement_in_slots(reserve_slots, *args_for_placement)
    else:
        slots_to_search = primary_slots + reserve_slots
        if not is_large_room_course: 
            random.shuffle(slots_to_search)
        best_placement = _find_best_greedy_placement_in_slots(slots_to_search, *args_for_placement)

    if best_placement:
        d_idx, s_idx, room = best_placement["day_idx"], best_placement["slot_idx"], best_placement["room"]
        details = {"id": lecture['id'], "name": lecture['name'], "teacher_name": teacher, "room": room, "room_type": lecture['room_type']}
        
        # --- بداية التصحيح ---
        # استخدام حلقة للمرور على قائمة المستويات بدلاً من المفتاح المفرد
        levels_for_lecture = lecture.get('levels', [])
        for level_to_place_in in levels_for_lecture:
            if level_to_place_in in final_schedule:
                final_schedule[level_to_place_in][d_idx][s_idx].append(details)
        # --- نهاية التصحيح ---
            
        teacher_schedule.setdefault(teacher, set()).add((d_idx, s_idx))
        room_schedule.setdefault(room, set()).add((d_idx, s_idx))
        if not teacher_constraints.get(teacher, {}).get('allowed_days'):
            teacher_constraints.setdefault(teacher, {}).setdefault('assigned_days', set()).add(d_idx)
        return True, "تمت الجدولة بنجاح في أفضل مكان"
    
    return False, "لم يتم العثور على أي فترة زمنية متاحة تحقق كل القيود."


# ================== استبدل الدالة القديمة بهذه النسخة المبسطة والصحيحة ==================
# استبدل دالة solve_backtracking بالكامل بهذه النسخة
def solve_backtracking(log_q, lectures_to_schedule, domains, final_schedule, teacher_schedule, room_schedule, teacher_constraints, special_constraints, identifiers_by_level, rules_grid, globally_unavailable_slots, rooms_data, start_time, timeout, lectures_by_teacher_map, distribution_rule_type, saturday_teachers, teacher_pairs, day_to_idx, initial_lecture_count, scheduling_state, level_specific_large_rooms, specific_small_room_assignments, num_slots, consecutive_large_hall_rule, max_sessions_per_day=None):
    if scheduling_state.get('should_stop'):
        raise StopByUserException()
    
    if time.time() - start_time > timeout:
        raise TimeoutException()

    num_placed = initial_lecture_count - len(lectures_to_schedule)
    if (num_placed > 0) and (num_placed % 10 == 0):
        log_q.put(f'   - البحث مستمر... تم توزيع {num_placed} / {initial_lecture_count} مادة')
        time.sleep(0)

    if not lectures_to_schedule:
        failures_list = validate_teacher_constraints_in_solution(teacher_schedule, special_constraints, teacher_constraints, lectures_by_teacher_map, distribution_rule_type, saturday_teachers, teacher_pairs, day_to_idx, [], num_slots, max_sessions_per_day)
        return not failures_list

    min_remaining_values = min(len(domains[lec['id']]) for lec in lectures_to_schedule) if lectures_to_schedule else 0
    if not lectures_to_schedule or min_remaining_values == 0 : return False

    most_constrained_lectures = [lec for lec in lectures_to_schedule if len(domains[lec['id']]) == min_remaining_values]
    
    next_lecture_to_schedule = max(most_constrained_lectures, key=lambda lec: len(lectures_by_teacher_map.get(lec.get('teacher_name'), [])))
    
    remaining_lectures = [lec for lec in lectures_to_schedule if lec['id'] != next_lecture_to_schedule['id']]
    current_lecture = next_lecture_to_schedule
    
    lecture_id = current_lecture['id']
    teacher_name = current_lecture['teacher_name']
    
    # ✨ التعامل مع قائمة المستويات
    levels_for_lecture = current_lecture.get('levels', [])
    lecture_room_type_needed = current_lecture.get('room_type')

    for day_idx, slot_idx, room in list(domains[lecture_id]):
        if (day_idx, slot_idx) in teacher_schedule.get(teacher_name, set()): continue
        if (day_idx, slot_idx) in room_schedule.get(room, set()): continue

        # ✨ حلقة للتحقق من الصلاحية في كل المستويات
        is_valid_for_all_levels = True
        for level in levels_for_lecture:
            if lecture_room_type_needed == 'كبيرة':
                required_room = level_specific_large_rooms.get(level)
                if required_room and room != required_room:
                    is_valid_for_all_levels = False; break
            
            if lecture_room_type_needed == 'صغيرة':
                course_full_name = f"{current_lecture.get('name')} ({level})"
                required_room = specific_small_room_assignments.get(course_full_name)
                if required_room and room != required_room:
                    is_valid_for_all_levels = False; break

            lectures_in_slot = final_schedule[level][day_idx][slot_idx]
            if lectures_in_slot and (lecture_room_type_needed == 'كبيرة' or any(lec.get('room_type') == 'كبيرة' for lec in lectures_in_slot)):
                is_valid_for_all_levels = False; break

            identifiers_for_level = identifiers_by_level.get(level, [])
            current_lecture_identifier = get_contained_identifier(current_lecture['name'], identifiers_for_level)
            if current_lecture_identifier:
                used_identifiers_in_slot = {get_contained_identifier(p_lec['name'], identifiers_for_level) for p_lec in lectures_in_slot}
                if current_lecture_identifier in used_identifiers_in_slot:
                    is_valid_for_all_levels = False; break
        
        if not is_valid_for_all_levels:
            continue

        details = {"id": current_lecture['id'], "name": current_lecture['name'], "teacher_name": teacher_name, "room": room, "room_type": lecture_room_type_needed}
        
        # ✨ التوزيع على كل المستويات
        for level in levels_for_lecture:
            final_schedule[level][day_idx][slot_idx].append(details)
        teacher_schedule.setdefault(teacher_name, set()).add((day_idx, slot_idx))
        room_schedule.setdefault(room, set()).add((day_idx, slot_idx))
        
        temp_domains = {lec_id: set(dom) for lec_id, dom in domains.items()}
        
        if solve_backtracking(log_q, remaining_lectures, temp_domains, final_schedule, teacher_schedule, room_schedule, teacher_constraints, special_constraints, identifiers_by_level, rules_grid, globally_unavailable_slots, rooms_data, start_time, timeout, lectures_by_teacher_map, distribution_rule_type, saturday_teachers, teacher_pairs, day_to_idx, initial_lecture_count, scheduling_state, level_specific_large_rooms, specific_small_room_assignments, num_slots, consecutive_large_hall_rule, max_sessions_per_day):
            return True

        room_schedule[room].discard((day_idx, slot_idx))
        teacher_schedule[teacher_name].discard((day_idx, slot_idx))
        # ✨ التراجع عن التوزيع في كل المستويات
        for level in levels_for_lecture:
            final_schedule[level][day_idx][slot_idx].pop()

    return False
# ================== نهاية الدالة المُعدلة بالكامل ==================



# استبدل الدالة بالكامل بهذه النسخة المحدثة
def calculate_schedule_cost(
    schedule, days, slots, teachers, rooms_data, levels, 
    identifiers_by_level, special_constraints, teacher_constraints, 
    distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, 
    saturday_teachers, teacher_pairs, day_to_idx, rules_grid, 
    last_slot_restrictions, level_specific_large_rooms, 
    specific_small_room_assignments, max_sessions_per_day=None, consecutive_large_hall_rule="none"
):
    conflicts_list = []
    all_lectures_map = {lec['id']: lec for lec in lectures_by_teacher_map.get('__all_lectures__', [])}

    # --- الخطوة 1: الكشف الفعال عن تعارضات الأساتذة والقاعات (لا تغيير هنا) ---
    for day_idx, day_name in enumerate(days):
        for slot_idx, slot_name in enumerate(slots):
            # تجميع المحاضرات من كل المستويات في هذه الفترة الزمنية
            lectures_in_this_slot = []
            for level in levels:
                if schedule.get(level) and day_idx < len(schedule[level]) and slot_idx < len(schedule[level][day_idx]):
                    lectures_in_this_slot.extend(schedule[level][day_idx][slot_idx])
            
            if not lectures_in_this_slot:
                continue

            # تجميع المحاضرات حسب الـ ID للتعرف على المواد المشتركة وتجنب اعتبارها تعارضاً
            lectures_by_id = defaultdict(list)
            for lec in lectures_in_this_slot:
                lectures_by_id[lec.get('id')].append(lec)

            teachers_in_slot_set = set()
            rooms_in_slot_set = set()
            for lec_id, lecture_group in lectures_by_id.items():
                rep_lec = lecture_group[0] 
                teacher, room = rep_lec.get('teacher_name'), rep_lec.get('room')
                
                if teacher and teacher in teachers_in_slot_set:
                    conflicts_list.append({"course_name": rep_lec.get('name'), "teacher_name": teacher, "reason": f"تعارض الأستاذ في {day_name} {slot_name}"})
                if teacher: teachers_in_slot_set.add(teacher)
                    
                if room and room in rooms_in_slot_set:
                    conflicts_list.append({"course_name": rep_lec.get('name'), "teacher_name": "N/A", "reason": f"تعارض في القاعة {room} في {day_name} {slot_name}"})
                if room: rooms_in_slot_set.add(room)

    # --- الخطوة 2: بناء الخرائط والتحقق الشامل من القيود الأخرى ---
    shared_lecture_placements = defaultdict(list)
    teacher_schedule_map = defaultdict(set)

    for level, day_grid in schedule.items():
        for day_idx, slot_list in enumerate(day_grid):
            for slot_idx, lectures in enumerate(slot_list):
                if not lectures: continue
                
                day_name, slot_name = days[day_idx], slots[slot_idx]
                
                if (day_idx, slot_idx) in globally_unavailable_slots:
                    conflicts_list.append({"course_name": "فترة راحة", "reason": f"خرق فترة الراحة العامة في {day_name} {slot_name}"})

                # --- 🔴 إصلاح: إضافة المنطق المفقود لقواعد الفترة الزمنية 🔴 ---
                rules_for_slot = rules_grid[day_idx][slot_idx]
                if rules_for_slot:
                    for lec in lectures:
                        is_level_in_any_rule, allowed_room_types = False, []
                        for rule in rules_for_slot:
                            if level in rule.get('levels', []):
                                is_level_in_any_rule = True
                                rule_type = rule.get('rule_type')
                                if rule_type == 'ANY_HALL': allowed_room_types.extend(['كبيرة', 'صغيرة'])
                                elif rule_type == 'SMALL_HALLS_ONLY': allowed_room_types.append('صغيرة')
                                elif rule_type == 'SPECIFIC_LARGE_HALL': allowed_room_types.append('كبيرة')
                        
                        if is_level_in_any_rule and lec.get('room_type') not in set(allowed_room_types):
                            conflicts_list.append({"course_name": lec.get('name'), "reason": f"قيد الفترة في {day_name} {slot_name} يخرق قاعدة نوع القاعة ({lec.get('room_type')})"})

                used_identifiers = set()
                large_room_lectures = [lec for lec in lectures if lec.get('room_type') == 'كبيرة']
                # --- 💡 تحسين: تصويب منطق تعارض القاعة الكبيرة ---
                if len(large_room_lectures) > 1 or (len(large_room_lectures) == 1 and len(lectures) > 1):
                    conflicts_list.append({"course_name": "عدة مواد", "teacher_name": level, "reason": f"تعارض قاعة كبيرة مع مادة أخرى في {day_name} {slot_name}"})

                for lec in lectures:
                    teacher_schedule_map[lec.get('teacher_name')].add((day_idx, slot_idx))
                    original_lec = all_lectures_map.get(lec.get('id'))
                    if original_lec and len(original_lec.get('levels', [])) > 1:
                        shared_lecture_placements[lec.get('id')].append({'level': level, 'day_idx': day_idx, 'slot_idx': slot_idx, 'room': lec.get('room')})
                    
                    # --- 💡 تحسين: إضافة سياق لرسائل الخطأ ---
                    if lec.get('room_type') == 'كبيرة' and (room := level_specific_large_rooms.get(level)) and lec.get('room') != room:
                        conflicts_list.append({"course_name": lec.get('name'), "reason": f"قيد قاعة المستوى في {day_name} {slot_name}: يجب أن تكون في '{room}' وليس '{lec.get('room')}'"})
                    if lec.get('room_type') == 'صغيرة' and (room := specific_small_room_assignments.get(f"{lec.get('name')} ({level})")) and lec.get('room') != room:
                        conflicts_list.append({"course_name": lec.get('name'), "reason": f"قيد القاعة الصغيرة في {day_name} {slot_name}: يجب أن تكون في '{room}' وليس '{lec.get('room')}'"})
                    
                    identifier = get_contained_identifier(lec['name'], identifiers_by_level.get(level, []))
                    if identifier and identifier in used_identifiers:
                        conflicts_list.append({"course_name": lec.get('name'), "teacher_name": level, "reason": f"تعارض معرفات ({identifier}) في {day_name} {slot_name}"})
                    if identifier: used_identifiers.add(identifier)

    # --- الخطوة 3: التحقق من صحة توزيع المواد المشتركة (لا تغيير هنا) ---
    for lec_id, placements in shared_lecture_placements.items():
        original_lec = all_lectures_map.get(lec_id)
        required_levels, placed_levels = set(original_lec.get('levels', [])), {p['level'] for p in placements}
        if required_levels != placed_levels:
            conflicts_list.append({"course_name": original_lec['name'], "reason": f"توزيع ناقص/زائد للمادة المشتركة. مطلوب في {required_levels}، موضوعة في {placed_levels}"})
        if len(placements) > 1 and len(set((p['day_idx'], p['slot_idx'], p['room']) for p in placements)) > 1:
            conflicts_list.append({"course_name": original_lec['name'], "reason": "توزيع غير متناسق للمادة المشتركة."})
    
    # --- بداية الإضافة: التحقق من قيد توالي القاعات الكبيرة ---
    if consecutive_large_hall_rule != 'none':
        for level, day_grid in schedule.items():
            for day_idx, slot_list in enumerate(day_grid):
                # نبدأ من الفترة الثانية للتحقق مع الفترة التي قبلها
                for slot_idx in range(1, len(slot_list)):
                    current_lectures = slot_list[slot_idx]
                    previous_lectures = slot_list[slot_idx - 1]
                    
                    # ابحث عن القاعات الكبيرة المستخدمة في الفترتين
                    current_large_halls = {lec['room'] for lec in current_lectures if lec.get('room_type') == 'كبيرة'}
                    previous_large_halls = {lec['room'] for lec in previous_lectures if lec.get('room_type') == 'كبيرة'}
                    
                    # ابحث عن القاعات المشتركة
                    common_halls = current_large_halls.intersection(previous_large_halls)
                    
                    for hall in common_halls:
                        # إذا كانت القاعدة تمنع الكل، أو تمنع هذه القاعة تحديدًا
                        if consecutive_large_hall_rule == 'all' or consecutive_large_hall_rule == hall:
                            conflicts_list.append({
                                "course_name": f"قيد التوالي للمستوى {level}",
                                "teacher_name": "N/A",
                                "reason": f"حدث توالٍ غير مسموح به في القاعة الكبيرة '{hall}'."
                            })
    # --- نهاية الإضافة ---

    # --- الخطوة 4: التحقق من قيود الأساتذة العامة (لا تغيير هنا) ---
    validation_failures = validate_teacher_constraints_in_solution(
        teacher_schedule_map, special_constraints, teacher_constraints, 
        lectures_by_teacher_map, distribution_rule_type, 
        saturday_teachers, teacher_pairs, day_to_idx, 
        last_slot_restrictions, len(slots), max_sessions_per_day=max_sessions_per_day
    )
    conflicts_list.extend(validation_failures)
    
    # --- الخطوة 5: إزالة أي تكرارات محتملة في قائمة الأخطاء (لا تغيير هنا) ---
    unique_failures = [dict(t) for t in {tuple(sorted(d.items())) for d in conflicts_list}]
    
    return unique_failures


# =====================================================================
# START: TABU SEARCH 
# =====================================================================

def run_tabu_search(log_q, all_lectures, days, slots, rooms_data, teachers, levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, scheduling_state, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=None, initial_solution=None, max_iterations=1000, tabu_tenure=10, neighborhood_size=50, consecutive_large_hall_rule="none", progress_channel=None):
    
    UNPLACED_PENALTY = 1000
    
    # === بداية الكود المضاف لإصلاح الخطأ ===
    # هذا الجزء ضروري لتحديد الفترات الزمنية الصالحة لكل أستاذ
    all_possible_slots = [(d, s) for d in range(len(days)) for s in range(len(slots))]
    globally_valid_slots = {slot for slot in all_possible_slots if slot not in globally_unavailable_slots}

    teacher_specific_valid_slots = {}
    for teacher in teachers:
        teacher_name = teacher['name']
        manual_days = teacher_constraints.get(teacher_name, {}).get('allowed_days')
        if manual_days:
            teacher_specific_valid_slots[teacher_name] = {slot for slot in globally_valid_slots if slot[0] in manual_days}
        else:
            teacher_specific_valid_slots[teacher_name] = globally_valid_slots
    # === نهاية الكود المضاف ===

    primary_slots, reserve_slots = [], []
    for day_idx in range(len(days)):
        for slot_idx in range(len(slots)):
            is_primary = any(rule.get('rule_type') == 'SPECIFIC_LARGE_HALL' for rule in rules_grid[day_idx][slot_idx])
            (primary_slots if is_primary else reserve_slots).append((day_idx, slot_idx))

    if initial_solution:
        log_q.put("البحث المحظور: الانطلاق من الحل المُعطى.")
        current_solution = copy.deepcopy(initial_solution)
    else:
        log_q.put("البحث المحظور: الانطلاق من حل عشوائي.")
        current_solution = {level: [[[] for _ in slots] for _ in days] for level in levels}
        if not all_lectures or not days or not slots:
            return current_solution, 9999, ["بيانات الإدخال فارغة"]
        
        small_rooms = [r['name'] for r in rooms_data if r['type'] == 'صغيرة']
        large_rooms = [r['name'] for r in rooms_data if r['type'] == 'كبيرة']
        for lec in all_lectures:
            # الآن هذه المتغيرات معرفة بشكل صحيح
            valid_slots_for_lec = teacher_specific_valid_slots.get(lec.get('teacher_name'), globally_valid_slots)
            if valid_slots_for_lec:
                day_idx, slot_idx = random.choice(list(valid_slots_for_lec))
                lec_with_room = lec.copy()
                if lec['room_type'] == 'كبيرة' and large_rooms:
                    lec_with_room['room'] = random.choice(large_rooms)
                elif lec['room_type'] == 'صغيرة' and small_rooms:
                    lec_with_room['room'] = random.choice(small_rooms)
                else:
                    lec_with_room['room'] = None
                levels_for_lec = lec.get('levels', [])
                for level_name in levels_for_lec:
                    if level_name in current_solution:
                        current_solution[level_name][day_idx][slot_idx].append(lec_with_room)

    best_solution = copy.deepcopy(current_solution)
    
    scheduled_ids_initial = {lec.get('id') for grid in current_solution.values() for day in grid for slot in day for lec in slot}
    unplaced_lectures_initial = [lec for lec in all_lectures if lec.get('id') not in scheduled_ids_initial]
    
    best_cost_list = calculate_schedule_cost(current_solution, days, slots, teachers, rooms_data, levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)
    
    best_cost = len(best_cost_list) + (len(unplaced_lectures_initial) * UNPLACED_PENALTY)
    current_cost = best_cost


    log_q.put(f"البحث المحظور: التكلفة الأولية = {current_cost} (تعارضات={len(best_cost_list)}, نقص={len(unplaced_lectures_initial)})")
    tabu_list = deque(maxlen=tabu_tenure)

    stagnation_counter = 0
    max_stagnation = max(100, int(max_iterations * 0.20))
    stagnation_violations = []

    for i in range(max_iterations):
        if scheduling_state.get('should_stop'):
            raise StopByUserException()
        
        time.sleep(0)
        if current_cost == 0:
            log_q.put("تم العثور على حل مثالي (التكلفة=0)!")
            break

        if stagnation_counter >= max_stagnation:
            log_q.put(f"   - ركود البحث لـ {stagnation_counter} دورة. تفعيل وضع التنويع الموجه...")
            
            problematic_teacher_counts = defaultdict(int)
            for violation in stagnation_violations:
                if violation.get('teacher_name'):
                    problematic_teacher_counts[violation['teacher_name']] += 1
            
            sorted_problematic_teachers = sorted(problematic_teacher_counts.items(), key=lambda item: item[1], reverse=True)
            profs_to_diversify = [teacher_name for teacher_name, count in sorted_problematic_teachers]

            if not profs_to_diversify:
                log_q.put("   - لم يتم العثور على أساتذة محددين للمشكلة، سيتم اختيارهم عشوائياً.")
                profs_to_diversify = [t['name'] for t in teachers] if teachers else []

            num_prof_to_diversify = max(1, int(len(teachers) * 0.3))
            final_profs_to_diversify = profs_to_diversify[:num_prof_to_diversify]
            
            if final_profs_to_diversify:
                 log_q.put(f"   - استهداف الأساتذة: {', '.join(final_profs_to_diversify)}")

            lectures_to_reinsert = [lec for lec in all_lectures if lec.get('teacher_name') in final_profs_to_diversify]
            ids_to_remove = {lec.get('id') for lec in lectures_to_reinsert}

            for level_grid in current_solution.values():
                for day_slots in level_grid:
                    for slot_lectures in day_slots:
                        slot_lectures[:] = [lec for lec in slot_lectures if lec.get('id') not in ids_to_remove]
            
            temp_teacher_schedule_div = defaultdict(set)
            temp_room_schedule_div = defaultdict(set)
            for grid in current_solution.values():
                for day_idx, day in enumerate(grid):
                    for slot_idx, lectures in enumerate(day):
                        for lec in lectures:
                            temp_teacher_schedule_div[lec['teacher_name']].add((day_idx, slot_idx))
                            if lec.get('room'): 
                                temp_room_schedule_div[lec['room']].add((day_idx, slot_idx))
            
            for lecture in lectures_to_reinsert:
                find_slot_for_single_lecture(lecture, current_solution, temp_teacher_schedule_div, temp_room_schedule_div, days, slots, rules_grid, rooms_data, teacher_constraints, globally_unavailable_slots, special_constraints, primary_slots, reserve_slots, identifiers_by_level, True, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule)

            div_scheduled_ids = {lec['id'] for grid in current_solution.values() for day in grid for slot in day for lec in slot}
            div_unplaced = [lec for lec in all_lectures if lec['id'] not in div_scheduled_ids]
            div_violations = calculate_schedule_cost(current_solution, days, slots, teachers, rooms_data, levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)
            
            current_cost = len(div_violations) + (len(div_unplaced) * UNPLACED_PENALTY)
            
            tabu_list.clear()
            stagnation_counter = 0 
            stagnation_violations.clear()
            log_q.put(f"   - اكتمل التنويع. التكلفة الجديدة = {current_cost} (نقص: {len(div_unplaced)}, تعارضات: {len(div_violations)})")
            continue

        failures_list = calculate_schedule_cost(current_solution, days, slots, teachers, rooms_data, levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)
        
        best_neighbor, best_neighbor_cost, move_to_make = None, float('inf'), None
        
        conflicting_courses = {f.get('course_name') for f in failures_list}
        conflicting_lectures = [lec for lec in all_lectures if lec.get('name') in conflicting_courses]

        for _ in range(neighborhood_size):
            if conflicting_lectures:
                lec_to_move = random.choice(conflicting_lectures)
            elif all_lectures:
                lec_to_move = random.choice(all_lectures)
            else:
                continue
            
            # الآن هذه المتغيرات معرفة بشكل صحيح
            teacher_of_lec_to_move = lec_to_move.get('teacher_name')
            valid_slots_for_move = teacher_specific_valid_slots.get(teacher_of_lec_to_move, globally_valid_slots)
            
            if not valid_slots_for_move: continue

            new_day_idx, new_slot_idx = random.choice(list(valid_slots_for_move))
            
            new_room = None
            large_rooms = [r['name'] for r in rooms_data if r['type'] == 'كبيرة']
            small_rooms = [r['name'] for r in rooms_data if r['type'] == 'صغيرة']

            if lec_to_move['room_type'] == 'كبيرة' and large_rooms:
                new_room = random.choice(large_rooms)
            elif lec_to_move['room_type'] == 'صغيرة' and small_rooms:
                new_room = random.choice(small_rooms)
            
            potential_move = (lec_to_move['id'], new_day_idx, new_slot_idx, new_room)

            neighbor_solution = copy.deepcopy(current_solution)
            lec_id_to_move = lec_to_move.get('id')
            for level_grid in neighbor_solution.values():
                for day_slots in level_grid:
                    for slot_lectures in day_slots:
                        slot_lectures[:] = [lec for lec in slot_lectures if lec.get('id') != lec_id_to_move]
            
            lec_with_new_room = lec_to_move.copy()
            lec_with_new_room['room'] = new_room
            levels_for_lec_to_move = lec_to_move.get('levels', [])
            for level_name in levels_for_lec_to_move:
                if level_name in neighbor_solution:
                    neighbor_solution[level_name][new_day_idx][new_slot_idx].append(lec_with_new_room)
            
            neighbor_cost_list = calculate_schedule_cost(neighbor_solution, days, slots, teachers, rooms_data, levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)
            
            hard_penalty = 0
            for day_grid in neighbor_solution.values():
                for slot_lectures in day_grid:
                    for lectures in slot_lectures:
                        if len(lectures) > 1 and any(lec.get('room_type') == 'كبيرة' for lec in lectures):
                            hard_penalty += 5000  # إضافة عقوبة كبيرة جدًا

            neighbor_scheduled_ids = {lec.get('id') for grid in neighbor_solution.values() for day in grid for slot in day for lec in slot}
            neighbor_unplaced = [lec for lec in all_lectures if lec.get('id') not in neighbor_scheduled_ids]
            neighbor_cost_val = len(neighbor_cost_list) + (len(neighbor_unplaced) * UNPLACED_PENALTY)

            if potential_move not in tabu_list or neighbor_cost_val < best_cost:
                if neighbor_cost_val < best_neighbor_cost:
                    best_neighbor_cost = neighbor_cost_val
                    best_neighbor = neighbor_solution
                    move_to_make = potential_move
        
        if best_neighbor is None:
            stagnation_counter += 1
            stagnation_violations.extend(failures_list)
            continue

        current_solution = best_neighbor
        current_cost = best_neighbor_cost
        if move_to_make:
             tabu_list.append(move_to_make)
        
        if current_cost < best_cost:
            best_cost = current_cost
            best_solution = copy.deepcopy(current_solution)
            if progress_channel: progress_channel['best_solution_so_far'] = best_solution
            stagnation_counter = 0
            stagnation_violations.clear()
            
            num_unplaced_now = best_cost // UNPLACED_PENALTY
            num_violations_now = best_cost % UNPLACED_PENALTY
            log_q.put(f"   - دورة {i+1}: تم العثور على حل أفضل. تكلفة={best_cost} (نقص: {num_unplaced_now}, تعارضات: {num_violations_now})")
            
            progress_percentage = max(0, (10 - num_violations_now) / 10 * 100) if num_unplaced_now == 0 else 5.0
            log_q.put(f"PROGRESS:{progress_percentage:.1f}")
        else:
            stagnation_counter += 1
            stagnation_violations.extend(failures_list)


    log_q.put(f"انتهى البحث المحظور. أفضل تكلفة تم الوصول إليها = {best_cost}")
    final_detailed_failures = calculate_schedule_cost(best_solution, days, slots, teachers, rooms_data, levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)

    final_scheduled_ids = {lec.get('id') for grid in best_solution.values() for day in grid for slot in day for lec in slot}
    final_unplaced_lectures = [
        {"course_name": lec.get('name'), "teacher_name": lec.get('teacher_name'), "reason": "المادة لم يتم جدولتها في الحل النهائي (نقص)."}
        for lec in all_lectures if lec.get('id') not in final_scheduled_ids
    ]
    
    final_detailed_failures.extend(final_unplaced_lectures)
    final_cost = len(final_detailed_failures)
    
    return best_solution, final_cost, final_detailed_failures



# =====================================================================
# START: GENETIC ALGORITHM CODE
# =====================================================================

def calculate_fitness(schedule, days, slots, teachers, rooms_data, levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=None, consecutive_large_hall_rule="none"):
    """
    تحسب "جودة" أو "لياقة" الحل. النتيجة تكون بين 0 و 1. كلما اقتربت من 1 كان الحل أفضل.
    """
    # نستدعي دالتنا القديمة لحساب عدد الأخطاء
    errors_list = calculate_schedule_cost(schedule, days, slots, teachers, rooms_data, levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)
    cost = len(errors_list)
    
    # نحول التكلفة (الأخطاء) إلى قيمة جودة (Fitness)
    # نضيف 1 للمقام لتجنب القسمة على صفر إذا كانت التكلفة 0
    fitness = 1.0 / (1.0 + cost)
    return fitness, errors_list


# النسخة النهائية والمكتملة للخوارزمية الجينية
def run_genetic_algorithm(log_q, lectures_to_schedule, days, slots, rooms_data, teachers, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, ga_population_size, ga_generations, ga_mutation_rate, ga_elitism_count, rules_grid, scheduling_state, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=None, initial_solution_seed=None, consecutive_large_hall_rule="none", progress_channel=None):
    
    
    log_q.put('--- بدء الخوارزمية الجينية ---')
    
    # 1. إنشاء الجيل الأول
    log_q.put(f'   - جاري إنشاء الجيل الأول ({ga_population_size} حل)...')
    population = create_initial_population(ga_population_size, lectures_to_schedule, days, slots, rooms_data, all_levels, level_specific_large_rooms, specific_small_room_assignments)
    time.sleep(0)

    # --- ✨ بداية الإضافة الجديدة: زرع البذرة (الحل الطماع) ---
    if initial_solution_seed:
        log_q.put('   - تم دمج الحل المبدئي (الطماع) في الجيل الأول.')
        if population:
            # استبدال الحل العشوائي الأول بالحل الأفضل القادم من الخوارزمية الطماعة
            population[0] = initial_solution_seed
    # --- نهاية الإضافة الجديدة ---

    best_solution_so_far = None
    best_fitness_so_far = -1.0
    
    # 2. حلقة التطور عبر الأجيال
    for gen in range(ga_generations):
        if scheduling_state.get('should_stop'):
            raise StopByUserException()
        # --- بداية التعديل: حساب أفضل تكلفة وعرضها مع كل جيل ---
        # نحسب أفضل تكلفة تم التوصل إليها حتى الآن من الجيل السابق
        best_cost_so_far = int(1/best_fitness_so_far - 1) if best_fitness_so_far > 0 else float('inf')
        # نعرضها في رسالة المتابعة الرئيسية

        log_q.put(f'--- الجيل {gen + 1}/{ga_generations} | أفضل عدد أخطاء = {best_cost_so_far} ---')
        time.sleep(0)
        # --- نهاية التعديل ---

        # تقييم جودة كل حل في الجيل الحالي
        population_with_fitness = []
        for schedule in population:
            fitness, _ = calculate_fitness(schedule, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day)
            population_with_fitness.append((schedule, fitness))
        
        population_with_fitness.sort(key=lambda item: item[1], reverse=True)

        # تحديث أفضل حل تم العثور عليه إذا وجدنا حلاً أفضل في هذا الجيل
        if population_with_fitness[0][1] > best_fitness_so_far:
            best_fitness_so_far = population_with_fitness[0][1]
            best_solution_so_far = copy.deepcopy(population_with_fitness[0][0])
            if progress_channel: progress_channel['best_solution_so_far'] = best_solution_so_far
            # نرسل رسالة خاصة ومميزة فقط عند حدوث تحسن فعلي
            cost = int(1/best_fitness_so_far - 1) if best_fitness_so_far > 0 else float('inf')
            
            log_q.put(f'   >>> إنجاز جديد! تم الوصول إلى فشل في القيود = {cost}')
            progress_percentage = max(0, (10 - cost) / 10 * 100)
            log_q.put(f"PROGRESS:{progress_percentage:.1f}")

        if best_fitness_so_far == 1.0:
            log_q.put('   - تم العثور على حل مثالي! إنهاء البحث.')
            break

        # الحفاظ على النخبة
        next_generation = [schedule for schedule, fitness in population_with_fitness[:ga_elitism_count]]
        
        # اختيار الآباء وإنتاج الأبناء
        offspring_to_produce = ga_population_size - ga_elitism_count
        
        for _ in range(offspring_to_produce // 2):
            parent1 = select_one_parent_tournament(population_with_fitness)
            parent2 = select_one_parent_tournament(population_with_fitness)
            child1, child2 = crossover(parent1, parent2, all_levels)
            next_generation.append(mutate(child1, lectures_to_schedule, days, slots, rooms_data, ga_mutation_rate))
            if len(next_generation) < ga_population_size:
                next_generation.append(mutate(child2, lectures_to_schedule, days, slots, rooms_data, ga_mutation_rate))
        
        population = next_generation
        # --- نهاية الجزء المضاف ---

    # 3. إرجاع أفضل حل تم العثور عليه
    
    log_q.put('انتهت الخوارزمية الجينية.')

    if not best_solution_so_far:
        best_solution_so_far = population_with_fitness[0][0] if population_with_fitness else create_initial_population(1, lectures_to_schedule, days, slots, rooms_data, all_levels)[0]

    _, final_cost_list = calculate_fitness(best_solution_so_far, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day)
    
    return best_solution_so_far, len(final_cost_list), final_cost_list

# =====================================================================
# END: GENETIC ALGORITHM CODE
# =====================================================================

# =====================================================================
# START: REINFORCEMENT LEARNING HELPERS (IMPROVED)
# =====================================================================

def get_state_from_failures_dominant(failures, unplaced_lectures_count):
    """
    تحول قائمة الأخطاء إلى تمثيل "حالة" مبسط وقوي.
    تركز الحالة على المشكلة الأكثر إلحاحًا: المواد الناقصة أولاً،
    ثم نوع الخطأ الأكثر تكرارًا.
    """
    # الأولوية القصوى هي وجود مواد لم يتم جدولتها
    if unplaced_lectures_count > 0:
        return "UNPLACED_LECTURES"

    if not failures:
        return "NO_ERRORS"

    # تحديد أنواع الأخطاء الرئيسية وتجميعها
    error_type_map = {
        'تعارض الأستاذ': 'CLASH_TEACHER',
        'تعارض في القاعة': 'CLASH_ROOM',
        'قيد التوزيع': 'DISTRIBUTION',
        'قيد وقت البدء': 'TIME_CONSTRAINT',
        'قيد وقت الإنهاء': 'TIME_CONSTRAINT',
        'قيد السبت': 'DAY_CONSTRAINT',
        'قيد الأزواج': 'PAIR_CONSTRAINT',
        'تعارض معرفات': 'IDENTIFIER_CLASH'
    }

    # حساب تكرار كل نوع خطأ رئيسي
    error_counts = defaultdict(int)
    for f in failures:
        reason = f.get('reason', '')
        # البحث عن الكلمة المفتاحية في سبب الخطأ لتحديد نوعه
        found_type = 'OTHER'
        for key, error_type in error_type_map.items():
            if key in reason:
                found_type = error_type
                break
        error_counts[found_type] += 1

    # إرجاع نوع الخطأ الأكثر تكرارًا كحالة
    if not error_counts:
        return "LOW_PRIORITY_ERRORS"
        
    dominant_error = max(error_counts, key=error_counts.get)
    return dominant_error


def calculate_reward_enhanced(old_cost, new_cost, old_unplaced, new_unplaced, time_taken):
    """
    تحسب المكافأة بناءً على التغير في التكلفة مع إعطاء أولوية للمواد الناقصة.
    """
    # مكافأة ضخمة جدًا على تقليل عدد المواد الناقصة
    if new_unplaced < old_unplaced:
        return (old_unplaced - new_unplaced) * 1000

    # عقوبة ضخمة على زيادة عدد المواد الناقصة
    if new_unplaced > old_unplaced:
        return (new_unplaced - old_unplaced) * -2000

    # إذا لم يتغير عدد المواد الناقصة، ننتقل لتقييم التعارضات
    cost_improvement = old_cost - new_cost

    if cost_improvement > 0:
        # مكافأة على تحسين عدد التعارضات
        return 50 + cost_improvement * 10
    elif cost_improvement == 0:
        # عقوبة صغيرة على الركود
        return -10
    else:
        # عقوبة أكبر على زيادة عدد التعارضات
        return -25 + cost_improvement * 5


# =====================================================================
# START: HYPER-HEURISTIC FRAMEWORK (FINAL VERSION WITH 3 MODES)
# =====================================================================
def run_hyper_heuristic(
    log_q, all_lectures, days, slots, rooms_data, teachers, all_levels,
    identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type,
    lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs,
    day_to_idx, rules_grid, prioritize_primary, scheduling_state, last_slot_restrictions,
    level_specific_large_rooms, specific_small_room_assignments, initial_solution, max_sessions_per_day=None, consecutive_large_hall_rule="none",
    flexible_categories=None, hyper_heuristic_iterations=50,
    learning_rate=0.1, discount_factor=0.9, initial_epsilon=0.5,
    epsilon_decay_rate=0.995, min_epsilon=0.05, selected_llh=None,
    heuristic_tabu_tenure=3,
    budget_mode='time', llh_time_budget=5.0, llh_iterations=30,
    # --->>> المعامل الجديد لوضع الركود <<<---
    stagnation_limit=15
):
    log_q.put(f'--- بدء النظام الخبير (وضع التحكم: {budget_mode}) ---')

    # (فلترة وتهيئة الخوارزميات والمعلمات تبقى كما هي)
    all_available_llh = { "VNS_Flexible": run_vns_with_flex_assignments, "LNS": run_large_neighborhood_search, "Tabu_Search": run_tabu_search, "Memetic_Algorithm": run_memetic_algorithm, "Genetic_Algorithm": run_genetic_algorithm, "CLONALG": run_clonalg }
    if not selected_llh: selected_llh = list(all_available_llh.keys())
    low_level_heuristics = {name: func for name, func in all_available_llh.items() if name in selected_llh}
    if not low_level_heuristics:
        log_q.put("   - تحذير: لم يتم اختيار أي خوارزميات. سيعود بالحل المبدئي.")
        initial_failures = calculate_schedule_cost(initial_solution, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day, consecutive_large_hall_rule)
        return initial_solution, len(initial_failures), initial_failures
    actions = list(low_level_heuristics.keys())
    q_table = defaultdict(lambda: {action: 0.0 for action in actions})
    epsilon = initial_epsilon
    time_budgets = {action: llh_time_budget for action in actions}
    MIN_BUDGET, MAX_BUDGET = 2.0, 20.0
    tabu_list = deque(maxlen=heuristic_tabu_tenure)

    # (حساب الحالة المبدئية يبقى كما هو)
    initial_scheduled_ids = {lec['id'] for grid in initial_solution.values() for day in grid for slot in day for lec in slot}
    initial_unplaced_count = len([lec for lec in all_lectures if lec.get('id') not in initial_scheduled_ids and lec.get('teacher_name')])
    initial_failures = calculate_schedule_cost(initial_solution, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day, consecutive_large_hall_rule)
    current_solution, current_failures, current_unplaced_count = copy.deepcopy(initial_solution), initial_failures, initial_unplaced_count
    current_cost = len(current_failures)
    best_solution_so_far, best_cost_so_far, best_unplaced_so_far = copy.deepcopy(current_solution), current_cost, current_unplaced_count
    log_q.put(f'   - الحل المبدئي: {current_cost} تعارضات, {current_unplaced_count} مواد ناقصة.')

    for i in range(hyper_heuristic_iterations):
        if scheduling_state.get('should_stop'): raise StopByUserException()
        if best_cost_so_far == 0 and best_unplaced_so_far == 0:
            log_q.put('   - تم العثور على حل مثالي! إنهاء البحث.'); break

        if i == 0 or (i + 1) % 5 == 0:
            log_q.put(f"--- 📊 الحالة الحالية: أفضل تعارضات = {best_cost_so_far}, أفضل نقص = {best_unplaced_so_far} ---")

        # (منطق الاختيار يبقى كما هو)
        current_state = get_state_from_failures_dominant(current_failures, current_unplaced_count)
        available_actions = [action for action in actions if action not in tabu_list]
        if not available_actions: available_actions = actions
        action = random.choice(available_actions) if random.random() < epsilon else max({act: q_table[current_state][act] for act in available_actions}, key=q_table[current_state].get)
        log_q.put(f'--- [دورة {i+1}/{hyper_heuristic_iterations}] | الحالة: {current_state} | اختيار: {action} ---')
        tabu_list.append(action)
        chosen_heuristic_func = low_level_heuristics[action]
        time.sleep(0.05)
        
        # (تجهيز المعاملات الأساسية والخاصة يبقى كما هو)
        base_params = { "progress_channel": None, "log_q": queue.Queue(), "all_lectures": all_lectures, "days": days, "slots": slots, "rooms_data": rooms_data, "teachers": teachers, "all_levels": all_levels, "identifiers_by_level": identifiers_by_level, "special_constraints": special_constraints, "teacher_constraints": teacher_constraints, "distribution_rule_type": distribution_rule_type, "lectures_by_teacher_map": lectures_by_teacher_map, "globally_unavailable_slots": globally_unavailable_slots, "saturday_teachers": saturday_teachers, "teacher_pairs": teacher_pairs, "day_to_idx": day_to_idx, "rules_grid": rules_grid, "scheduling_state": scheduling_state, "last_slot_restrictions": last_slot_restrictions, "level_specific_large_rooms": level_specific_large_rooms, "specific_small_room_assignments": specific_small_room_assignments, "max_sessions_per_day": max_sessions_per_day, "consecutive_large_hall_rule": consecutive_large_hall_rule }
        specific_params = {}
        if action == "VNS_Flexible":
            teacher_schedule_from_best = defaultdict(set); room_schedule_from_best = defaultdict(set)
            for grid in current_solution.values():
                for d_idx, day in enumerate(grid):
                    for s_idx, lectures in enumerate(day):
                        for lec in lectures:
                            if lec.get('teacher_name'): teacher_schedule_from_best[lec['teacher_name']].add((d_idx, s_idx))
                            if lec.get('room'): room_schedule_from_best[lec.get('room')].add((d_idx, s_idx))
            specific_params = {"k_max": 5, "initial_schedule": copy.deepcopy(current_solution), "initial_teacher_schedule": teacher_schedule_from_best, "initial_room_schedule": room_schedule_from_best, "flexible_categories": flexible_categories, "prioritize_primary": prioritize_primary}
        elif action == "LNS":
            specific_params = {"ruin_factor": 0.3, "prioritize_primary": prioritize_primary}
        elif action == "Tabu_Search":
            if 'all_levels' in base_params: base_params['levels'] = base_params.pop('all_levels')
            specific_params = {"tabu_tenure": 15, "neighborhood_size": 50, "initial_solution": copy.deepcopy(current_solution)}
        elif action in ["Memetic_Algorithm", "Genetic_Algorithm"]:
            if 'all_lectures' in base_params: base_params['lectures_to_schedule'] = base_params.pop('all_lectures')
            if action == "Memetic_Algorithm":
                specific_params = {"ma_population_size": 20, "ma_mutation_rate": 0.1, "ma_elitism_count": 2, "ma_local_search_iterations": 5, "initial_solution_seed": copy.deepcopy(current_solution), "prioritize_primary": prioritize_primary}
            else:
                specific_params = {"ga_population_size": 40, "ga_mutation_rate": 0.05, "ga_elitism_count": 2, "initial_solution_seed": copy.deepcopy(current_solution)}
        elif action == "CLONALG":
            if 'all_lectures' in base_params: base_params['lectures_to_schedule'] = base_params.pop('all_lectures')
            specific_params = {"population_size": 40, "selection_size": 8, "clone_factor": 1.0, "initial_solution_seed": copy.deepcopy(current_solution)}

        # --->>> بداية الهيكلة الجديدة للتحكم في التشغيل (الآن 3 أوضاع) <<<---
        if budget_mode == 'time':
            current_time_budget = time_budgets[action]
            log_q.put(f"   -- منح {current_time_budget:.1f} ثانية لخوارزمية {action}.")
            if action in ["VNS_Flexible", "LNS", "Tabu_Search"]: specific_params['max_iterations'] = 99999
            elif action == "Memetic_Algorithm":
                specific_params['ma_generations'] = 9999
            elif action == "Genetic_Algorithm":
                specific_params['ga_generations'] = 9999
            elif action == "CLONALG":
                specific_params['generations'] = 9999
            
            local_timeout_state = {'should_stop': False}; progress_channel = {'best_solution_so_far': None}
            def timeout_watcher(state, budget): time.sleep(budget); state['should_stop'] = True
            threading.Thread(target=timeout_watcher, args=(local_timeout_state, current_time_budget), daemon=True).start()
            def user_stop_monitor(global_state, local_state):
                while not local_state.get('should_stop') and not global_state.get('should_stop'): time.sleep(0.2)
                if global_state.get('should_stop'): local_state['should_stop'] = True
            threading.Thread(target=user_stop_monitor, args=(scheduling_state, local_timeout_state), daemon=True).start()
            base_params['scheduling_state'] = local_timeout_state
            base_params['progress_channel'] = progress_channel
            try:
                temp_solution, _, _ = chosen_heuristic_func(**base_params, **specific_params)
            except StopByUserException:
                temp_solution = progress_channel['best_solution_so_far'] or current_solution
        
        elif budget_mode == 'iterations':
            log_q.put(f"   -- منح {llh_iterations} دورة لخوارزمية {action}.")
            if action in ["VNS_Flexible", "LNS", "Tabu_Search"]: specific_params['max_iterations'] = llh_iterations
            elif action == "Memetic_Algorithm": specific_params['ma_generations'] = llh_iterations
            elif action == "Genetic_Algorithm": specific_params['ga_generations'] = llh_iterations
            elif action == "CLONALG": specific_params['generations'] = llh_iterations
            temp_solution, _, _ = chosen_heuristic_func(**base_params, **specific_params)

        else: # budget_mode == 'stagnation'
            log_q.put(f"   -- وضع التوقف عند الركود (مهلة: {stagnation_limit} ثانية)...")
            if action in ["VNS_Flexible", "LNS", "Tabu_Search"]: 
                specific_params['max_iterations'] = 999999
            elif action == "Memetic_Algorithm":
                specific_params['ma_generations'] = 99999
            elif action == "Genetic_Algorithm":
                specific_params['ga_generations'] = 99999
            elif action == "CLONALG":
                specific_params['generations'] = 99999

            local_state = {'should_stop': False}
            # --- ✅ بداية التعديل: تهيئة قناة التقدم بالحل الحالي ---
            progress_channel = {'best_solution_so_far': copy.deepcopy(current_solution)}
            # --- نهاية التعديل ---

            base_params['scheduling_state'] = local_state
            base_params['progress_channel'] = progress_channel
            
            # المهمة 1: الخوارزمية الفرعية (العامل)
            llh_thread = threading.Thread(target=chosen_heuristic_func, kwargs={**base_params, **specific_params})

            # المهمة 2: مراقب الركود (المشرف)
            def stagnation_monitor():
                # --- ✅ بداية التعديل: البدء من الحل المعروف مسبقاً ---
                last_known_solution = progress_channel['best_solution_so_far']
                # --- نهاية التعديل ---
                last_progress_time = time.time()
                llh_thread.start() # بدء عمل العامل
                while llh_thread.is_alive():
                    if scheduling_state.get('should_stop'): # تحقق من أمر المستخدم أولاً
                        local_state['should_stop'] = True; break
                    
                    current_solution_in_channel = progress_channel.get('best_solution_so_far')
                    if current_solution_in_channel is not last_known_solution:
                        last_progress_time = time.time() # إعادة ضبط المؤقت عند إحراز تقدم
                        last_known_solution = current_solution_in_channel

                    if time.time() - last_progress_time > stagnation_limit:
                        log_q.put(f"   -- تم اكتشاف ركود لأكثر من {stagnation_limit} ثانية. إيقاف الخوارزمية...")
                        local_state['should_stop'] = True; break
                    time.sleep(0.5)
            
            stagnation_monitor() # بدء عمل المشرف
            llh_thread.join() # انتظار العامل لينتهي
            temp_solution = progress_channel.get('best_solution_so_far') or current_solution

        # (بقية الكود الخاص بالتقييم وتحديث الذاكرة يبقى كما هو)
        temp_scheduled_ids = {lec['id'] for grid in temp_solution.values() for day in grid for slot in day for lec in slot}
        temp_unplaced_count = len([lec for lec in all_lectures if lec.get('id') not in temp_scheduled_ids and lec.get('teacher_name')])
        temp_failures = calculate_schedule_cost(temp_solution, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day, consecutive_large_hall_rule)
        temp_cost = len(temp_failures)
        reward = calculate_reward_enhanced(current_cost, temp_cost, current_unplaced_count, temp_unplaced_count, 0)
        new_state = get_state_from_failures_dominant(temp_failures, temp_unplaced_count)
        old_value = q_table[current_state][action]
        next_max = max(q_table[new_state].values()) if q_table[new_state] else 0.0
        new_value = old_value + learning_rate * (reward + discount_factor * next_max - old_value)
        q_table[current_state][action] = new_value
        log_q.put(f"   -- المكافأة: {reward:.1f}. تحديث خبرة ({action}) في '{current_state}' إلى: {new_value:.2f}")

        if budget_mode == 'time':
            REWARD_SCALE = 100.0; MAX_TIME_CHANGE_PER_ITERATION = 2.5
            time_change_capped = max(-MAX_TIME_CHANGE_PER_ITERATION, min(MAX_TIME_CHANGE_PER_ITERATION, (reward / REWARD_SCALE)))
            time_budgets[action] = max(MIN_BUDGET, min(MAX_BUDGET, time_budgets[action] + time_change_capped))
            log_q.put(f"   -- (تغيير الوقت: {time_change_capped:+.2f} ثانية) | الميزانية الجديدة لـ {action}: {time_budgets[action]:.1f} ثانية.")

        current_solution, current_cost, current_failures, current_unplaced_count = temp_solution, temp_cost, temp_failures, temp_unplaced_count
        if (current_unplaced_count < best_unplaced_so_far) or (current_unplaced_count == best_unplaced_so_far and current_cost < best_cost_so_far):
            best_unplaced_so_far, best_cost_so_far, best_solution_so_far = current_unplaced_count, current_cost, copy.deepcopy(current_solution)
            log_q.put(f'   >>> ✅ إنجاز! {action} قلل الأخطاء إلى (تعارضات: {best_cost_so_far}, نقص: {best_unplaced_so_far})')
            progress_percentage = max(0, (10 - best_cost_so_far) / 10 * 100) if best_unplaced_so_far == 0 else 5.0
            log_q.put(f"PROGRESS:{progress_percentage:.1f}")
        if epsilon > min_epsilon: epsilon *= epsilon_decay_rate

    # (كود نهاية الدالة يبقى كما هو)
    log_q.put('انتهى عمل النظام الخبير.')
    final_failures = calculate_schedule_cost(best_solution_so_far, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day, consecutive_large_hall_rule)
    final_scheduled_ids = {lec['id'] for grid in best_solution_so_far.values() for day in grid for slot in day for lec in slot}
    final_unplaced = [{"course_name": lec.get('name'), "teacher_name": lec.get('teacher_name'), "reason": "نقص في الحل النهائي."} for lec in all_lectures if lec.get('id') not in final_scheduled_ids and lec.get('teacher_name')]
    final_failures.extend(final_unplaced)
    return best_solution_so_far, len(final_failures), final_failures

def run_local_search_on_schedule(
    schedule_to_improve, all_lectures, days, slots, rooms_data, teachers, all_levels, 
    identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, 
    lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, 
    day_to_idx, rules_grid, prioritize_primary, level_specific_large_rooms, 
    specific_small_room_assignments, max_iterations=10, last_slot_restrictions=None, max_sessions_per_day=None, consecutive_large_hall_rule="none"
):
    """
    تأخذ جدولاً وتحاول تحسينه عبر إجراء عدد من محاولات النقل المحلية الذكية.
    """
    improved_schedule = copy.deepcopy(schedule_to_improve)
    
    for iteration in range(max_iterations):
        # 1. تحديد الأخطاء الحالية لتوجيه البحث
        current_failures = calculate_schedule_cost(
            improved_schedule, days, slots, teachers, rooms_data, all_levels,
            identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type,
            lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs,
            day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms,
            specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule
        )

        if not current_failures:
            break # لا توجد أخطاء، الحل مثالي

        # 2. اختيار مادة تسبب مشكلة لمحاولة نقلها
        lecture_to_move = None
        # استخراج أسماء المواد من قائمة الأخطاء
        failure_course_names = {f.get('course_name') for f in current_failures}
        conflicting_lectures = [lec for lec in all_lectures if lec.get('name') in failure_course_names]
        
        if conflicting_lectures:
            lecture_to_move = random.choice(conflicting_lectures)
        elif all_lectures:
            # إذا لم نجد مادة مرتبطة بخطأ مباشر، نختار واحدة عشوائياً
            lecture_to_move = random.choice(all_lectures)
        else:
            continue # لا توجد مواد لنقلها

        lec_id_to_move = lecture_to_move.get('id')

        # 3. بناء جداول الحجوزات الحالية (يجب تحديثها في كل دورة)
        teacher_schedule_ls = defaultdict(set)
        room_schedule_ls = defaultdict(set)
        for level_grid in improved_schedule.values():
            for d_idx, day in enumerate(level_grid):
                for s_idx, lectures in enumerate(day):
                    for lec in lectures:
                        teacher_schedule_ls[lec['teacher_name']].add((d_idx, s_idx))
                        if lec.get('room'):
                            room_schedule_ls[lec.get('room')].add((d_idx, s_idx))
        
        # 4. (تصحيح جوهري) البحث عن المادة وإزالتها من **جميع** أماكنها الحالية
        original_position_info = None
        
        for level_name, day_grid in improved_schedule.items():
            for d_idx, day_slots in enumerate(day_grid):
                for s_idx, lectures_in_slot in enumerate(day_slots):
                    lec_instance = next((lec for lec in lectures_in_slot if lec.get('id') == lec_id_to_move), None)
                    if lec_instance:
                        if original_position_info is None:
                            original_position_info = (d_idx, s_idx, lec_instance.get('room'))
                        
                        # إزالة المادة من هذا المكان
                        improved_schedule[level_name][d_idx][s_idx] = [l for l in lectures_in_slot if l.get('id') != lec_id_to_move]

        if not original_position_info:
            continue

        # بمجرد إزالة المادة بالكامل، نحدث جداول الحجوزات
        d_idx_orig, s_idx_orig, room_orig = original_position_info
        teacher_schedule_ls[lecture_to_move['teacher_name']].discard((d_idx_orig, s_idx_orig))
        if room_orig:
            room_schedule_ls[room_orig].discard((d_idx_orig, s_idx_orig))

        # 5. محاولة إيجاد مكان أفضل لها باستخدام الخوارزمية الطماعية
        primary_slots, reserve_slots = [], []
        for day_idx in range(len(days)):
            for slot_idx in range(len(slots)):
                is_primary = any(rule.get('rule_type') == 'SPECIFIC_LARGE_HALL' for rule in rules_grid[day_idx][slot_idx])
                (primary_slots if is_primary else reserve_slots).append((day_idx, slot_idx))

        success, _ = find_slot_for_single_lecture(
            lecture_to_move, improved_schedule, teacher_schedule_ls, room_schedule_ls,
            days, slots, rules_grid, rooms_data, teacher_constraints, globally_unavailable_slots, 
            special_constraints, primary_slots, reserve_slots, identifiers_by_level,
            prioritize_primary, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule
        )
        
        # 6. إذا فشلت المحاولة، أعد المادة إلى مكانها الأصلي بشكل صحيح
        if not success:
            d_idx, s_idx, room = original_position_info
            details = {**lecture_to_move, 'room': room}
            
            levels_for_lecture = lecture_to_move.get('levels', [])
            for level_name in levels_for_lecture:
                if level_name in improved_schedule:
                    improved_schedule[level_name][d_idx][s_idx].append(details)
            
            # لا حاجة لتحديث جداول الحجوزات هنا لأننا سنعيد بنائها في الدورة التالية

    return improved_schedule

# =====================================================================
# START: MEMETIC ALGORITHM (GA + Local Search)
# =====================================================================
def run_memetic_algorithm(log_q, lectures_to_schedule, days, slots, rooms_data, teachers, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, prioritize_primary, ma_population_size, ma_generations, ma_mutation_rate, ma_elitism_count, ma_local_search_iterations, scheduling_state, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=None, initial_solution_seed=None, consecutive_large_hall_rule="none", progress_channel=None):
    
    
    log_q.put('--- بدء الخوارزمية الميميتيك (GA + LS) ---')
    
    # 1. إنشاء الجيل الأول (نفس طريقة GA)
    log_q.put(f'   - جاري إنشاء الجيل الأول ({ma_population_size} حل)...')
    
    population = create_initial_population(ma_population_size, lectures_to_schedule, days, slots, rooms_data, all_levels, level_specific_large_rooms, specific_small_room_assignments)
    time.sleep(0)

     # --- ✨ بداية الإضافة الجديدة: زرع البذرة (الحل الطماع) ---
    if initial_solution_seed:
        log_q.put('   - تم دمج الحل المبدئي (الطماع) في الجيل الأول.')
        
        if population:
            # استبدال الحل العشوائي الأول بالحل الأفضل القادم من الخوارزمية الطماعة
            population[0] = initial_solution_seed
    # --- نهاية الإضافة الجديدة ---

    best_solution_so_far = None
    best_fitness_so_far = -1.0
    
    # 2. حلقة التطور عبر الأجيال
    for gen in range(ma_generations):
        if scheduling_state.get('should_stop'):
            raise StopByUserException()
        best_cost_so_far = int(1/best_fitness_so_far - 1) if best_fitness_so_far > 0 else float('inf')
        
        log_q.put(f'--- الجيل {gen + 1}/{ma_generations} | أفضل عدد أخطاء = {best_cost_so_far} ---')
        time.sleep(0)

        # تقييم جودة كل حل في الجيل الحالي
        population_with_fitness = []
        for schedule in population:
            fitness, _ = calculate_fitness(schedule, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)
            population_with_fitness.append((schedule, fitness))
        
        population_with_fitness.sort(key=lambda item: item[1], reverse=True)

        if population_with_fitness[0][1] > best_fitness_so_far:
            best_fitness_so_far = population_with_fitness[0][1]
            best_solution_so_far = copy.deepcopy(population_with_fitness[0][0])
            if progress_channel: progress_channel['best_solution_so_far'] = best_solution_so_far
            cost = int(1/best_fitness_so_far - 1) if best_fitness_so_far > 0 else float('inf')
            
            log_q.put(f'   >>> إنجاز جديد! تم الوصول إلى فشل في القيود = {cost}')
            progress_percentage = max(0, (10 - cost) / 10 * 100)
            log_q.put(f"PROGRESS:{progress_percentage:.1f}")

        if best_fitness_so_far == 1.0:
            log_q.put('   - تم العثور على حل مثالي! إنهاء البحث.')
            break

        # الحفاظ على النخبة
        next_generation = [schedule for schedule, fitness in population_with_fitness[:ma_elitism_count]]
        
        # إنتاج بقية الجيل الجديد
        offspring_to_produce = ma_population_size - ma_elitism_count
        
        for _ in range(offspring_to_produce // 2):
            if not population_with_fitness: break
            parent1 = select_one_parent_tournament(population_with_fitness)
            parent2 = select_one_parent_tournament(population_with_fitness)
            child1, child2 = crossover(parent1, parent2, all_levels)
            
            # تطبيق الطفرة
            mutated_child1 = mutate(child1, lectures_to_schedule, days, slots, rooms_data, ma_mutation_rate)
            
            # ===*** التحسين المحلي (هذا هو قلب الخوارزمية الميميتيك) ***===
            improved_child1 = run_local_search_on_schedule(
                mutated_child1, lectures_to_schedule, days, slots, rooms_data, teachers, all_levels, 
                identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, 
                lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, 
                day_to_idx, rules_grid, prioritize_primary, level_specific_large_rooms, specific_small_room_assignments,
                max_iterations=ma_local_search_iterations, consecutive_large_hall_rule=consecutive_large_hall_rule
            )
            next_generation.append(improved_child1)
            
            if len(next_generation) < ma_population_size:
                mutated_child2 = mutate(child2, lectures_to_schedule, days, slots, rooms_data, ma_mutation_rate)
                improved_child2 = run_local_search_on_schedule(
                    mutated_child2, lectures_to_schedule, days, slots, rooms_data, teachers, all_levels, 
                    identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, 
                    lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, 
                    day_to_idx, rules_grid, prioritize_primary, level_specific_large_rooms, specific_small_room_assignments,
                    max_iterations=ma_local_search_iterations, consecutive_large_hall_rule=consecutive_large_hall_rule
                )
                next_generation.append(improved_child2)
        
        population = next_generation

    
    log_q.put('انتهت الخوارزمية الميميتيك.')

    if not best_solution_so_far:
        best_solution_so_far = population_with_fitness[0][0] if population_with_fitness else create_initial_population(1, lectures_to_schedule, days, slots, rooms_data, all_levels)[0]

    _, final_cost_list = calculate_fitness(best_solution_so_far, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)
    
    return best_solution_so_far, len(final_cost_list), final_cost_list

# =====================================================================
# END: MEMETIC ALGORITHM
# =====================================================================

def create_initial_population(population_size, lectures, days, slots, rooms_data, levels, level_specific_large_rooms, specific_small_room_assignments):
    population = []
    small_rooms = [r['name'] for r in rooms_data if r['type'] == 'صغيرة']
    large_rooms = [r['name'] for r in rooms_data if r['type'] == 'كبيرة']

    for _ in range(population_size):
        schedule = {level: [[[] for _ in slots] for _ in days] for level in levels}
        for lec in lectures:
            lec_with_room = lec.copy()
            
            # ✨ التصحيح يبدأ هنا
            # بما أن القاعة يجب أن تكون نفسها لكل المستويات، نستخدم المستوى الأول كمرجع لاختيارها
            first_level = lec.get('levels', [None])[0]

            if lec['room_type'] == 'كبيرة' and large_rooms:
                required_room = level_specific_large_rooms.get(first_level)
                if required_room and required_room in large_rooms:
                    lec_with_room['room'] = required_room
                else:
                    lec_with_room['room'] = random.choice(large_rooms)
            elif lec['room_type'] == 'صغيرة' and small_rooms:
                if first_level:
                    course_full_name = f"{lec.get('name')} ({first_level})"
                    required_room = specific_small_room_assignments.get(course_full_name)
                    if required_room and required_room in small_rooms:
                        lec_with_room['room'] = required_room
                    else:
                        lec_with_room['room'] = random.choice(small_rooms)
                else:
                     lec_with_room['room'] = random.choice(small_rooms)
            else:
                lec_with_room['room'] = None

            day_idx = random.randint(0, len(days) - 1)
            slot_idx = random.randint(0, len(slots) - 1)
            
            # ✨ المرور على كل مستويات المادة لوضعها في الجدول
            levels_for_lec = lec.get('levels', [])
            for level_name in levels_for_lec:
                if level_name in schedule:
                    schedule[level_name][day_idx][slot_idx].append(lec_with_room)
        
        population.append(schedule)
    return population

def select_one_parent_tournament(population_with_fitness, tournament_size=3):
    """
    تختار أصل (parent) واحد عن طريق إقامة "بطولة" مصغرة.
    تختار عدد من الحلول عشوائياً، والحل صاحب أعلى جودة يفوز.
    """
    # اختر k من الأفراد عشوائياً للمنافسة في البطولة
    tournament_contenders = random.sample(population_with_fitness, tournament_size)
    # الفائز هو صاحب أعلى قيمة جودة (fitness)
    winner = max(tournament_contenders, key=lambda item: item[1])
    # نرجع الحل الفائز (الكروموسوم) ليكون أباً
    return winner[0]

def crossover(parent1, parent2, all_levels):
    """
    تقوم بإنتاج طفلين عبر تبادل جداول المستويات بين الأبوين.
    """
    child1 = {level: [[] for _ in parent1[list(parent1.keys())[0]][0]] for level in all_levels}
    child2 = {level: [[] for _ in parent1[list(parent1.keys())[0]][0]] for level in all_levels}

    for level in all_levels:
        # 50% فرصة لتبادل المستوى بين الأبوين
        if random.random() < 0.5:
            child1[level] = parent1[level]
            child2[level] = parent2[level]
        else:
            child1[level] = parent2[level]
            child2[level] = parent1[level]
            
    return child1, child2

# استبدل دالة mutate بالكامل بهذه النسخة المصححة
def mutate(schedule, all_lectures, days, slots, rooms_data, mutation_rate):
    """
    تقوم بإجراء طفرة عشوائية على الجدول عبر نقل محاضرة واحدة.
    """
    # نستخدم deepcopy لضمان عدم تعديل الجدول الأصلي مباشرة
    mutated_schedule = copy.deepcopy(schedule)

    # نقرر أولاً ما إذا كانت الطفرة ستحدث بناءً على معدل الطفرة
    if random.random() >= mutation_rate:
        return mutated_schedule # لا تحدث طفرة، أعد الجدول كما هو

    # إذا حدثت الطفرة، قم بنقل محاضرة واحدة عشوائياً
    if not all_lectures:
        return mutated_schedule

    # 1. اختيار محاضرة عشوائية لنقلها
    lec_to_move = random.choice(all_lectures)
    
    # 2. ✨ الجزء الذي تم دمجه: البحث عن المحاضرة وإزالتها من مكانها الحالي
    lec_id_to_move = lec_to_move.get('id')
    for level_grid in mutated_schedule.values():
        for day_slots in level_grid:
            for slot_lectures in day_slots:
                # استخدام list comprehension لإنشاء قائمة جديدة بدون المحاضرة المحذوفة
                # هذا أأمن وأنظف من الحذف أثناء المرور على القائمة
                slot_lectures[:] = [lec for lec in slot_lectures if lec.get('id') != lec_id_to_move]

    # 3. وضع المحاضرة في مكان عشوائي جديد
    new_day_idx = random.randint(0, len(days) - 1)
    new_slot_idx = random.randint(0, len(slots) - 1)
    
    # نعيد استخدام المنطق من دالة الإنشاء الأولي
    lec_with_room = lec_to_move.copy()
    small_rooms = [r['name'] for r in rooms_data if r['type'] == 'صغيرة']
    large_rooms = [r['name'] for r in rooms_data if r['type'] == 'كبيرة']
    if lec_to_move['room_type'] == 'كبيرة' and large_rooms:
        lec_with_room['room'] = random.choice(large_rooms)
    elif lec_to_move['room_type'] == 'صغيرة' and small_rooms:
        lec_with_room['room'] = random.choice(small_rooms)
    else:
        lec_with_room['room'] = None

    # إضافة المحاضرة لكل مستوياتها في المكان الجديد
    levels_for_lec = lec_to_move.get('levels', [])
    for level_name in levels_for_lec:
        if level_name in mutated_schedule:
            mutated_schedule[level_name][new_day_idx][new_slot_idx].append(lec_with_room)

    return mutated_schedule


# ابحث عن هذه الدالة في ملف app.py واستبدلها بالكامل بهذا الكود
@app.route('/api/generate-schedule', methods=['POST'])
def generate_schedule():
    
    def run_scheduling_task(settings, courses, rooms_data, all_levels, teachers, identifiers_by_level, scheduling_state, log_q):
        try:
            courses_original_state = copy.deepcopy(courses)
            # --- استخراج الكائنات الفرعية أولاً ---
            phase_5_settings = settings.get('phase_5_settings', {})
            algorithm_settings = settings.get('algorithm_settings', {})
            flexible_categories = settings.get('flexible_categories', [])
            
            # --- الآن نقرأ كل الإعدادات من الكائنات الصحيحة ---
            intensive_attempts = int(algorithm_settings.get('intensive_search_attempts', 1))
            max_sessions_per_day_str = algorithm_settings.get('max_sessions_per_day', 'none')
            max_sessions_per_day = int(max_sessions_per_day_str) if max_sessions_per_day_str.isdigit() else None
            
            if intensive_attempts > 1:
                log_q.put(f"--- بدء البحث المكثف لـ {intensive_attempts} محاولات ---")
                
            
            all_results = []

            for attempt in range(intensive_attempts):
                if scheduling_state.get('should_stop'):
                    raise StopByUserException()
                
                failures = []
                
                if intensive_attempts > 1:
                    log_q.put(f"\n--- المحاولة رقم {attempt + 1} / {intensive_attempts} ---")
                    time.sleep(0.1)

                # --- الجزء 1: تجهيز بنية الجدول الأساسية (مشترك للجميع) ---
                days, slots, day_to_idx, slot_to_idx, rules_grid = process_schedule_structure(settings.get('schedule_structure'))
                num_days, num_slots = len(days), len(slots)
                
                # --- الجزء 2: المعالجة المسبقة بناءً على الخوارزمية المختارة (الهيكل الجديد والصحيح) ---
                method = algorithm_settings.get('method', 'greedy')
                
                lectures_to_schedule = []
                # سيتم استخدام هذه المتغيرات كقاعدة للانطلاق
                initial_final_schedule = {} 
                initial_teacher_schedule = {}
                initial_room_schedule = {}

                if method == 'vns_flexible':
                    # --- الحالة الأولى: VNS المرنة، مع استعادة آلية تسجيل الفشل ---
                    log_q.put("--- (VNS-F): تفعيل منطق تثبيت المواد مع تسجيل الأخطاء ---")
                    
                    # 1. جهز جداول فارغة
                    initial_final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                    initial_teacher_schedule = {t['name']: set() for t in teachers}
                    initial_room_schedule = {r['name']: set() for r in rooms_data}
                    
                    # ## استعادة: تهيئة قائمة لتسجيل محاولات التثبيت الفاشلة
                    failures = [] 
                    
                    # 2. قم بتثبيت المواد وحجز أماكنها
                    pinned_lectures_to_exclude = set()
                    courses_map = {c['id']: c for c in courses}
                    small_rooms = [r['name'] for r in rooms_data if r['type'] == 'صغيرة']
                    large_rooms = [r['name'] for r in rooms_data if r['type'] == 'كبيرة']

                    schedule_structure = settings.get('schedule_structure', {})
                    for day_name, day_slots in schedule_structure.items():
                        day_idx = day_to_idx.get(day_name)
                        if day_idx is None: continue

                        for time_key, slot_info in day_slots.items():
                            slot_idx = slot_to_idx.get(time_key)
                            pinned_course_id = slot_info.get('pinnedCourseId')

                            if slot_idx is None or not pinned_course_id: continue
                            
                            lecture = courses_map.get(pinned_course_id)
                            
                            # ## استعادة: التحقق من وجود المادة وإسنادها وطباعة تحذير
                            if not lecture or not lecture.get('teacher_name'):
                                log_q.put(f"تحذير: تم تجاهل تثبيت المادة (ID: {pinned_course_id}) لأنها غير موجودة أو غير مسندة لأستاذ.")
                                continue
                            
                            teacher_name = lecture['teacher_name']
                            room_pool = large_rooms if lecture['room_type'] == 'كبيرة' else small_rooms
                            
                            # ## استعادة: التحقق من وجود قاعات وتسجيل الفشل
                            if not room_pool:
                                failures.append({"course_name": lecture['name'], "teacher_name": teacher_name, "reason": f"فشل التثبيت: لا توجد قاعات متاحة من نوع '{lecture['room_type']}'."})
                                continue
                            
                            available_room = next((r for r in room_pool if (day_idx, slot_idx) not in initial_room_schedule.get(r, set())), None)
                            
                            # ## استعادة: التحقق من التعارضات وتسجيل سبب الفشل
                            if not available_room or (day_idx, slot_idx) in initial_teacher_schedule.get(teacher_name, set()):
                                reason = "لا توجد قاعة شاغرة" if not available_room else "تعارض مع جدول الأستاذ"
                                failures.append({"course_name": lecture['name'], "teacher_name": teacher_name, "reason": f"فشل التثبيت: {reason} في {day_name} {time_key}."})
                                continue

                            # التثبيت الناجح (لا تغيير هنا)
                            details = {"id": lecture['id'], "name": lecture['name'], "teacher_name": teacher_name, "room": available_room, "room_type": lecture['room_type']}
                            for level in lecture.get('levels', []):
                                if level in initial_final_schedule:
                                    initial_final_schedule[level][day_idx][slot_idx].append(details)
                            
                            initial_teacher_schedule.setdefault(teacher_name, set()).add((day_idx, slot_idx))
                            initial_room_schedule.setdefault(available_room, set()).add((day_idx, slot_idx))
                            pinned_lectures_to_exclude.add(pinned_course_id)
                    
                    # 3. جهز قائمة المواد مع استبعاد المواد التي تم تثبيتها بالفعل
                    lectures_to_schedule = [c for c in courses if c.get('id') not in pinned_lectures_to_exclude]

                else:
                    # --- الحالة الثانية: لأي خوارزمية أخرى، تجاهل التثبيت وجهز قائمة كاملة ---
                    log_q.put(f"--- ({method}): جدولة كل المواد المسندة بدون تثبيت ---")
                    
                    # جهز قائمة المواد **بكل المواد المسندة لأستاذ**
                    lectures_to_schedule = [c for c in courses if c.get('teacher_name')]
                    # الجداول المبدئية ستبقى فارغة، والخوارزميات ستبدأ من الصفر
                    initial_final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                    initial_teacher_schedule = {t['name']: set() for t in teachers}
                    initial_room_schedule = {r['name']: set() for r in rooms_data}


                # --- الجزء 3: تهيئة القيود والمتغيرات (الجزء الهام الذي كان مفقودًا) ---
                prioritize_primary = algorithm_settings.get('prioritize_primary', False)
                teacher_pairs_text = algorithm_settings.get('teacher_pairs_text', '')
                consecutive_large_hall_rule = algorithm_settings.get('consecutive_large_hall_rule', 'none')
                distribution_rule_type = algorithm_settings.get('distribution_rule_type', 'allowed')
                
                manual_days = phase_5_settings.get('manual_days', {})
                special_constraints = phase_5_settings.get('special_constraints', {})
                saturday_teachers = phase_5_settings.get('saturday_teachers', [])
                last_slot_restrictions = phase_5_settings.get('last_slot_restrictions', {})
                level_specific_large_rooms = phase_5_settings.get('level_specific_large_rooms', {})
                specific_small_room_assignments = phase_5_settings.get('specific_small_room_assignments', {})
                rest_periods = phase_5_settings.get('rest_periods', {})
                
                teacher_pairs = []
                if teacher_pairs_text:
                    for line in teacher_pairs_text.strip().split('\n'):
                        parts = [name.strip() for name in line.split('،') if name.strip()]
                        if len(parts) == 2:
                            teacher_pairs.append(tuple(sorted(parts)))

                teacher_constraints = {t['name']: {} for t in teachers}
                for teacher_name, days_list in manual_days.items():
                    if teacher_name in teacher_constraints:
                        teacher_constraints[teacher_name]['allowed_days'] = {day_to_idx[d] for d in days_list if d in day_to_idx}
                
                globally_unavailable_slots = set()
                if rest_periods.get('tuesday_evening') and 'الثلاثاء' in day_to_idx and num_slots >= 2:
                    tuesday_idx = day_to_idx['الثلاثاء']
                    globally_unavailable_slots.add((tuesday_idx, num_slots - 2))
                    globally_unavailable_slots.add((tuesday_idx, num_slots - 1))
                if rest_periods.get('thursday_evening') and 'الخميس' in day_to_idx and num_slots >= 2:
                    thursday_idx = day_to_idx['الخميس']
                    globally_unavailable_slots.add((thursday_idx, num_slots - 2))
                    globally_unavailable_slots.add((thursday_idx, num_slots - 1))

                primary_slots, reserve_slots = [], []
                day_indices_shuffled = list(range(num_days)); random.shuffle(day_indices_shuffled)
                for day_idx in day_indices_shuffled:
                    for slot_idx in range(num_slots):
                        is_primary = any(rule.get('rule_type') == 'SPECIFIC_LARGE_HALL' for rule in rules_grid[day_idx][slot_idx])
                        (primary_slots if is_primary else reserve_slots).append((day_idx, slot_idx))


                # --- الجزء 4: تجهيز البيانات للجدولة (مشترك للجميع) ---
                lectures_by_teacher_map = defaultdict(list)
                for lec in lectures_to_schedule:
                    if lec.get('teacher_name'):
                        lectures_by_teacher_map[lec.get('teacher_name')].append(lec)
                lectures_by_teacher_map['__all_lectures__'] = lectures_to_schedule
                
                lectures_sorted = sorted(
                    lectures_to_schedule, 
                    key=lambda lec: calculate_lecture_difficulty(lec, lectures_by_teacher_map.get(lec.get('teacher_name'), []), special_constraints, manual_days), 
                    reverse=True
                )
                
                # --- الجزء 5: تجهيز الحل المبدئي الطماع للخوارزميات المتقدمة (الجزء المفقود الثاني) ---
                greedy_initial_schedule = None
                if method in ['tabu_search', 'large_neighborhood_search', 'variable_neighborhood_search', 'memetic_algorithm', 'clonalg', 'genetic_algorithm', 'hyper_heuristic']:
                    log_q.put(f"جاري تحضير حل مبدئي عبر الخوارزمية الطماعة لـ {method}...")
                    
                    # ابدأ بالجداول التي قد تحتوي على مواد مثبتة
                    greedy_initial_schedule = copy.deepcopy(initial_final_schedule)
                    temp_teacher_schedule = copy.deepcopy(initial_teacher_schedule)
                    temp_room_schedule = copy.deepcopy(initial_room_schedule)

                    # استخدم القائمة المرتبة التي تم إنشاؤها لجدولة بقية المواد
                    for lecture in lectures_sorted:
                        # الخوارزمية الطماعة ستجد مكانًا للمحاضرات غير المثبتة
                        find_slot_for_single_lecture(lecture, greedy_initial_schedule, temp_teacher_schedule, temp_room_schedule, days, slots, rules_grid, rooms_data, teacher_constraints, globally_unavailable_slots, special_constraints, primary_slots, reserve_slots, identifiers_by_level, prioritize_primary, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule)
                
                detailed_failures = []
                # الموزع الرئيسي
                if method == 'backtracking':
                    start_time = time.time()
                    timeout = int(algorithm_settings.get('timeout', 30))
                    
                    final_schedule, teacher_schedule, room_schedule = initial_final_schedule, initial_teacher_schedule, initial_room_schedule

                    log_q.put('خوارزمية التراجع: بدء مرحلة تحضير نطاقات البحث (قد تستغرق وقتاً)...')
                    domains = {}
                    total_lectures = len(lectures_to_schedule)
                    timeout_occured = False
                    for idx, lecture in enumerate(lectures_to_schedule):
                        # ---- تعديل: إضافة تفقد حالة الإيقاف هنا ----
                        if scheduling_state.get('should_stop'):
                            raise StopByUserException()
                        # -----------------------------------------
                        if time.time() - start_time > timeout:
                            timeout_occured = True
                            log_q.put(f"... انتهت مهلة البحث ({timeout} ثانية) أثناء مرحلة التحضير.")
                            break # الخروج من حلقة التحضير

                        if (idx > 0) and (idx % 20 == 0):
                            log_q.put(f'   - جاري تحضير المادة {idx} من {total_lectures}')
                            time.sleep(0)
                        
                        lecture_id = lecture['id']
                        lecture_domains = set()
                        is_large_room_course = lecture.get('room_type') == 'كبيرة'

                        if is_large_room_course and prioritize_primary:
                            for day_idx, slot_idx in primary_slots:
                                is_possible, result_room = is_placement_valid(lecture, day_idx, slot_idx, final_schedule, teacher_schedule, room_schedule, teacher_constraints, special_constraints, identifiers_by_level, rules_grid, globally_unavailable_slots, rooms_data, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule)
                                if is_possible:
                                    lecture_domains.add((day_idx, slot_idx, result_room))
                            if not lecture_domains:
                                for day_idx, slot_idx in reserve_slots:
                                    is_possible, result_room = is_placement_valid(lecture, day_idx, slot_idx, final_schedule, teacher_schedule, room_schedule, teacher_constraints, special_constraints, identifiers_by_level, rules_grid, globally_unavailable_slots, rooms_data, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule)
                                    if is_possible:
                                        lecture_domains.add((day_idx, slot_idx, result_room))
                        else:
                            slots_to_search_bt = primary_slots + reserve_slots
                            for day_idx, slot_idx in slots_to_search_bt:
                                is_possible, result_room = is_placement_valid(lecture, day_idx, slot_idx, final_schedule, teacher_schedule, room_schedule, teacher_constraints, special_constraints, identifiers_by_level, rules_grid, globally_unavailable_slots, rooms_data, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule)
                                if is_possible:
                                    lecture_domains.add((day_idx, slot_idx, result_room))
                        domains[lecture_id] = lecture_domains
                    
                    
                    if timeout_occured:
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": f"انتهت مهلة البحث ({timeout} ثانية) أثناء مرحلة التحضير الطويلة. حاول تبسيط القيود أو زيادة المهلة."})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                    else:
                        # --- نهاية الإضافة ---
                        log_q.put('... انتهت مرحلة التحضير. بدء البحث الفعلي.')
                    
                        try:
                            # ---- تعديل: تمرير حالة الإيقاف للدالة ----
                            solution_found = solve_backtracking(log_q, lectures_to_schedule, domains, final_schedule, teacher_schedule, room_schedule, teacher_constraints, special_constraints, identifiers_by_level, rules_grid, globally_unavailable_slots, rooms_data, start_time, timeout, lectures_by_teacher_map, distribution_rule_type, saturday_teachers, teacher_pairs, day_to_idx, total_lectures, scheduling_state, level_specific_large_rooms, specific_small_room_assignments, num_slots, consecutive_large_hall_rule, max_sessions_per_day)
                            if not solution_found:
                                failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": "فشلت الخوارزمية في إيجاد حل صالح يحقق جميع القيود المحددة. قد تكون القيود متضاربة أو شديدة الصعوبة."})
                                final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                        # ---- تعديل: إضافة معالجة استثناء الإيقاف اليدوي ----
                        except (TimeoutException, StopByUserException):
                            if scheduling_state.get('should_stop'):
                                failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": "تم إيقاف العملية من قبل المستخدم."})
                            else:
                                failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": f"انتهت مهلة البحث ({timeout} ثانية) قبل إيجاد حل. حاول زيادة المهلة الزمنية أو تبسيط القيود."})
                            final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}

                elif method == 'tabu_search':
                    max_iterations = int(algorithm_settings.get('tabu_iterations', 1000))
                    tabu_tenure = int(algorithm_settings.get('tabu_tenure', 10))
                    neighborhood_size = int(algorithm_settings.get('tabu_neighborhood_size', 50))
                    
                    if max_iterations > 5000:
                        log_q.put(f'تحذير: عدد التكرارات كبير جداً ({max_iterations}). سيتم تقليله إلى 5000 لتجنب التعليق.')
                        max_iterations = 5000
                    
                    try:
                        log_q.put(f'بدء البحث المحظور مع {max_iterations} تكرار.')
                        
                        
                                                
                        # --- بداية الإضافة: إعادة مراقب مهلة التشغيل ---
                        def timeout_monitor():
                            # ننتظر 10 دقائق (600 ثانية)
                            time.sleep(600)
                            # إذا لم تكن العملية قد توقفت بالفعل، نرسل تحذيراً
                            if not scheduling_state.get('should_stop'):
                                log_q.put('\n⚠️ تحذير: البحث المحظور يعمل لأكثر من 10 دقائق. اضغط إيقاف إذا أردت التوقف.')
                        
                        timeout_thread = threading.Thread(target=timeout_monitor, daemon=True)
                        timeout_thread.start()
                        # --- نهاية الإضافة ---

                        final_schedule, final_cost, detailed_failures = run_tabu_search(
                            log_q, 
                            lectures_to_schedule, 
                            days, 
                            slots, 
                            rooms_data, 
                            teachers, 
                            all_levels, 
                            identifiers_by_level, 
                            special_constraints, 
                            teacher_constraints, 
                            distribution_rule_type, 
                            lectures_by_teacher_map, 
                            globally_unavailable_slots, 
                            saturday_teachers, 
                            teacher_pairs, 
                            day_to_idx, 
                            rules_grid,
                            scheduling_state,
                            last_slot_restrictions,
                            level_specific_large_rooms,
                            specific_small_room_assignments,
                            initial_solution=greedy_initial_schedule,
                            max_iterations=max_iterations,
                            tabu_tenure=tabu_tenure,
                            neighborhood_size=neighborhood_size,
                            max_sessions_per_day=max_sessions_per_day,
                            consecutive_large_hall_rule=consecutive_large_hall_rule
                        )
                        
                        if final_cost > 0:
                            main_reason = f"انتهى البحث المحظور بأفضل جدول ممكن يحتوي على {final_cost} تعارضات."
                            failures.append({
                                "course_name": "N/A",
                                "teacher_name": "Tabu Search",
                                "reason": main_reason
                            })
                            failures_to_display = detailed_failures[:10]
                            for i, detail in enumerate(failures_to_display):
                                failures.append({
                                    "course_name": f"   - التفصيل #{i+1}",
                                    "teacher_name": "",
                                    "reason": str(detail)
                                })
                    
                    except StopByUserException:
                        log_q.put('\n--- تم إيقاف البحث المحظور من قبل المستخدم. ---')
                        
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": "تم إيقاف العملية من قبل المستخدم."})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                    
                    except Exception as e: 
                        traceback.print_exc()
                        log_q.put(f'\nحدث خطأ في البحث المحظور: {str(e)}')
                        
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": f"خطأ في الخوارزمية: {str(e)}"})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                    
                    finally:
                        log_q.put('\n=== انتهت عملية البحث المحظور نهائياً ===')
                        time.sleep(0.2)
                
                elif method == 'genetic_algorithm':
                    ga_population_size = int(algorithm_settings.get('ga_population_size', 50))
                    ga_generations = int(algorithm_settings.get('ga_generations', 200))
                    ga_mutation_rate = float(algorithm_settings.get('ga_mutation_rate', 5)) / 100.0
                    ga_elitism_count = int(algorithm_settings.get('ga_elitism_count', 2))

                    # === حماية من المعاملات المفرطة ===
                    if ga_population_size > 200:
                        log_q.put(f'تحذير: حجم المجتمع كبير جداً ({ga_population_size}). سيتم تقليله إلى 200.')
                        
                        ga_population_size = 200
                    
                    if ga_generations > 1000:
                        log_q.put(f'تحذير: عدد الأجيال كبير جداً ({ga_generations}). سيتم تقليله إلى 1000.')
                        
                        ga_generations = 1000

                    try:
                        log_q.put(f'بدء الخوارزمية الجينية: {ga_population_size} فرد، {ga_generations} جيل.')
                        
                        
                        # === مراقبة زمن التشغيل ===
                        
                        
                        
                        def timeout_monitor():
                            time.sleep(300)  # 5 دقائق
                            if not scheduling_state.get('should_stop'):
                                
                                log_q.put('\n⚠️ تحذير: الخوارزمية الجينية تعمل لأكثر من 5 دقائق. اضغط إيقاف إذا أردت التوقف.')
                        
                        timeout_thread = threading.Thread(target=timeout_monitor, daemon=True)
                        timeout_thread.start()

                        # تمرير حالة الإيقاف
                        final_schedule, final_cost, detailed_failures = run_genetic_algorithm(
                            log_q, lectures_to_schedule, days, slots, rooms_data, teachers, all_levels, 
                            identifiers_by_level, special_constraints, teacher_constraints, 
                            distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, 
                            saturday_teachers, teacher_pairs, day_to_idx, ga_population_size, 
                            ga_generations, ga_mutation_rate, ga_elitism_count, rules_grid,
                            scheduling_state, last_slot_restrictions, level_specific_large_rooms,
                            specific_small_room_assignments, initial_solution_seed=greedy_initial_schedule,
                            max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule
                        )
                        
                        if final_cost > 0:
                            main_reason = f"انتهت الخوارزمية الجينية بأفضل حل يحتوي على {final_cost} تعارضات."
                            failures.append({
                                "course_name": "N/A",
                                "teacher_name": "Genetic Algorithm",
                                "reason": main_reason
                            })
                            failures_to_display = detailed_failures[:10]
                            for i, detail in enumerate(failures_to_display):
                                failures.append({
                                    "course_name": f"   - التفصيل #{i+1}",
                                    "teacher_name": "",
                                    "reason": detail
                                })
                    
                    except StopByUserException:
                        log_q.put('\n--- تم إيقاف الخوارزمية الجينية من قبل المستخدم. ---')
                        
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": "تم إيقاف العملية من قبل المستخدم."})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                    
                    except Exception as e:
                        traceback.print_exc()
                        log_q.put(f'\nحدث خطأ في الخوارزمية الجينية: {str(e)}')
                        
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": f"خطأ في الخوارزمية: {str(e)}"})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                    
                    finally:
                        log_q.put('\n=== انتهت الخوارزمية الجينية نهائياً ===')
                        
                        time.sleep(0.2)

                # ابحث عن هذا الجزء في دالة generate_schedule وقم بتحديثه:

                elif method == 'large_neighborhood_search':
                    lns_iterations = int(algorithm_settings.get('lns_iterations', 500))
                    lns_ruin_factor = float(algorithm_settings.get('lns_ruin_factor', 20)) / 100.0

                    try:
                        # ---- تعديل: تمرير حالة الإيقاف للدالة مع معالجة الاستثناءات ----
                        final_schedule, final_cost, detailed_failures = run_large_neighborhood_search(
                            log_q, lectures_to_schedule, days, slots, rooms_data, teachers, all_levels, 
                            identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, 
                            lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, 
                            day_to_idx, rules_grid, lns_iterations, lns_ruin_factor, prioritize_primary,
                            scheduling_state, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments,
                            max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule
                        )
                        
                        if final_cost > 0:
                            main_reason = f"انتهت الخوارزمية بأفضل حل يحتوي على {final_cost} تعارضات."
                            failures.append({
                                "course_name": "N/A",
                                "teacher_name": "Large Neighborhood Search",
                                "reason": main_reason
                            })
                            failures_to_display = detailed_failures[:10]
                            for i, detail in enumerate(failures_to_display):
                                failures.append({
                                    "course_name": f"   - التفصيل #{i+1}",
                                    "teacher_name": "",
                                    "reason": detail
                                })
                    
                    # ---- إضافة معالجة استثناء الإيقاف اليدوي (مهم جداً) ----
                    except StopByUserException:
                        log_q.put('\n--- تم إيقاف البحث الجواري الواسع من قبل المستخدم. ---')
                        
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": "تم إيقاف العملية من قبل المستخدم."})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                    
                    except Exception as e:
                        log_q.put(f'\nحدث خطأ في البحث الجواري الواسع: {str(e)}')
                        
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": f"خطأ في الخوارزمية: {str(e)}"})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                
                elif method == 'variable_neighborhood_search':
                    vns_iterations = int(algorithm_settings.get('vns_iterations', 300))
                    vns_k_max = int(algorithm_settings.get('vns_k_max', 10))

                    try:
                        # ---- تعديل: تمرير حالة الإيقاف للدالة مع معالجة الاستثناءات ----
                        final_schedule, final_cost, detailed_failures = run_variable_neighborhood_search(
                            log_q, lectures_to_schedule, days, slots, rooms_data, teachers, all_levels, 
                            identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, 
                            lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, 
                            day_to_idx, rules_grid, vns_iterations, vns_k_max, prioritize_primary,
                            scheduling_state, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments,
                            max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule
                        )
                        
                        if final_cost > 0:
                            main_reason = f"انتهت الخوارزمية بأفضل حل يحتوي على {final_cost} تعارضات."
                            failures.append({
                                "course_name": "N/A",
                                "teacher_name": "Variable Neighborhood Search",
                                "reason": main_reason
                            })
                            failures_to_display = detailed_failures[:10]
                            for i, detail in enumerate(failures_to_display):
                                failures.append({
                                    "course_name": f"   - التفصيل #{i+1}",
                                    "teacher_name": "",
                                    "reason": detail
                                })
                    
                    # ---- إضافة معالجة استثناء الإيقاف اليدوي ----
                    except StopByUserException:
                        log_q.put('\n--- تم إيقاف البحث الجواري المتغير من قبل المستخدم. ---')
                        
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": "تم إيقاف العملية من قبل المستخدم."})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                    
                    except Exception as e:
                        log_q.put(f'\nحدث خطأ في البحث الجواري المتغير: {str(e)}')
                        
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": f"خطأ في الخوارزمية: {str(e)}"})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                
                elif method == 'vns_flexible':
                    algo_settings = settings.get('algorithm_settings', {})
                    vns_iterations = int(algo_settings.get('vns_iterations', 300))
                    vns_k_max = int(algo_settings.get('vns_k_max', 10))
                    flexible_categories = settings.get('flexible_categories', []) # <-- جلب البيانات الجديدة

                    try:
                        final_schedule = initial_final_schedule
                        teacher_schedule = initial_teacher_schedule
                        room_schedule = initial_room_schedule
                        final_schedule, final_cost, detailed_failures = run_vns_with_flex_assignments(
                            log_q, lectures_to_schedule, days, slots, rooms_data, teachers, all_levels, 
                            identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, 
                            lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, 
                            day_to_idx, rules_grid, vns_iterations, vns_k_max, prioritize_primary,
                            scheduling_state, last_slot_restrictions, level_specific_large_rooms, 
                            specific_small_room_assignments, flexible_categories,
                            initial_schedule=final_schedule,
                            initial_teacher_schedule=teacher_schedule,
                            initial_room_schedule=room_schedule,
                            max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule
                        )
                        
                        if final_cost > 0:
                            failures.append({"course_name": "N/A", "teacher_name": "VNS-Flexible", "reason": f"انتهت الخوارزمية بأفضل حل يحتوي على {final_cost} تعارضات."})
                            for detail in detailed_failures: failures.append(detail)
                    
                    except StopByUserException:
                        log_q.put('\n--- تم إيقاف البحث الجواري المتغير من قبل المستخدم. ---')
                        
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": "تم إيقاف العملية من قبل المستخدم."})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                    
                    except Exception as e:
                        log_q.put(f'\nحدث خطأ في البحث الجواري المتغير: {str(e)}')
                        
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": f"خطأ في الخوارزمية: {str(e)}"})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                
                elif method == 'memetic_algorithm':
                    ma_population_size = int(algorithm_settings.get('ma_population_size', 40))
                    ma_generations = int(algorithm_settings.get('ma_generations', 100))
                    ma_mutation_rate = float(algorithm_settings.get('ma_mutation_rate', 10)) / 100.0
                    ma_elitism_count = int(algorithm_settings.get('ma_elitism_count', 2))
                    ma_local_search_iterations = int(algorithm_settings.get('ma_local_search_iterations', 5))

                    try:
                        # ---- تعديل: تمرير حالة الإيقاف والحل المبدئي للدالة ----
                        final_schedule, final_cost, detailed_failures = run_memetic_algorithm(
                            log_q, lectures_to_schedule, days, slots, rooms_data, teachers, all_levels, 
                            identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, 
                            lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, 
                            day_to_idx, rules_grid, prioritize_primary, ma_population_size, ma_generations, 
                            ma_mutation_rate, ma_elitism_count, ma_local_search_iterations,
                            scheduling_state, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments,
                            initial_solution_seed=greedy_initial_schedule, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule
                        )

                        if final_cost > 0:
                            main_reason = f"انتهت الخوارزمية الميميتيك بأفضل حل يحتوي على {final_cost} تعارضات."
                            failures.append({
                                "course_name": "N/A",
                                "teacher_name": "Memetic Algorithm",
                                "reason": main_reason
                            })
                            failures_to_display = detailed_failures[:10]
                            for i, detail in enumerate(failures_to_display):
                                failures.append({
                                    "course_name": f"   - التفصيل #{i+1}",
                                    "teacher_name": "",
                                    "reason": detail
                                })
                    
                    except StopByUserException:
                        log_q.put('\n--- تم إيقاف الخوارزمية الميميتيك من قبل المستخدم. ---')
                        
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": "تم إيقاف العملية من قبل المستخدم."})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                    
                    except Exception as e:
                        log_q.put(f'\nحدث خطأ في الخوارزمية الميميتيك: {str(e)}')
                        
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": f"خطأ في الخوارزمية: {str(e)}"})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}

                elif method == 'clonalg':
                    clonalg_population_size = int(algorithm_settings.get('clonalg_population_size', 50))
                    clonalg_generations = int(algorithm_settings.get('clonalg_generations', 100))
                    clonalg_selection_size = int(algorithm_settings.get('clonalg_selection_size', 10))
                    clonalg_clone_factor = float(algorithm_settings.get('clonalg_clone_factor', 1.0))
                    
                    try:
                        # ---- تعديل: تمرير حالة الإيقاف والحل المبدئي للدالة ----
                        final_schedule, final_cost, detailed_failures = run_clonalg(
                            log_q, lectures_to_schedule, days, slots, rooms_data, teachers, all_levels, 
                            identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, 
                            lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, 
                            day_to_idx, rules_grid, clonalg_population_size, clonalg_generations, 
                            clonalg_selection_size, clonalg_clone_factor,
                            scheduling_state, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments,
                            initial_solution_seed=greedy_initial_schedule, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule
                        )

                        if final_cost > 0:
                            main_reason = f"انتهت خوارزمية الاستنساخ بأفضل حل يحتوي على {final_cost} تعارضات."
                            failures.append({
                                "course_name": "N/A",
                                "teacher_name": "CLONALG",
                                "reason": main_reason
                            })
                            failures_to_display = detailed_failures[:10]
                            for i, detail in enumerate(failures_to_display):
                                failures.append({
                                    "course_name": f"   - التفصيل #{i+1}",
                                    "teacher_name": "",
                                    "reason": detail
                                })
                    
                    except StopByUserException:
                        log_q.put('\n--- تم إيقاف خوارزمية الاستنساخ من قبل المستخدم. ---')
                        
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": "تم إيقاف العملية من قبل المستخدم."})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                    
                    except Exception as e:
                        log_q.put(f'\nحدث خطأ في خوارزمية الاستنساخ: {str(e)}')
                        
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": f"خطأ في الخوارزمية: {str(e)}"})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                
                elif method == 'hyper_heuristic':
                    try:
                        hh_iterations = int(algorithm_settings.get('hh_iterations', 50))
                        hh_selected_llh = algorithm_settings.get('hh_selected_llh', [])
                        hh_tabu_tenure = int(algorithm_settings.get('hh_tabu_tenure', 3))
                        # --->>> إضافة المتغيرات الجديدة <<<---
                        hh_budget_mode = algorithm_settings.get('hh_budget_mode', 'time')
                        hh_time_budget = float(algorithm_settings.get('hh_time_budget', 5.0))
                        hh_llh_iterations = int(algorithm_settings.get('hh_llh_iterations', 30))
                        hh_stagnation_limit = int(algorithm_settings.get('hh_stagnation_limit', 15))

                        final_schedule, final_cost, detailed_failures = run_hyper_heuristic(
                            log_q, lectures_to_schedule, days, slots, rooms_data, teachers, all_levels,
                            identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type,
                            lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs,
                            day_to_idx, rules_grid, prioritize_primary, scheduling_state, last_slot_restrictions,
                            level_specific_large_rooms, specific_small_room_assignments,
                            initial_solution=greedy_initial_schedule,
                            max_sessions_per_day=max_sessions_per_day,
                            consecutive_large_hall_rule=consecutive_large_hall_rule,
                            flexible_categories=flexible_categories,
                            hyper_heuristic_iterations=hh_iterations,
                            selected_llh=hh_selected_llh,
                            heuristic_tabu_tenure=hh_tabu_tenure,
                            # --->>> تمرير المعاملات الجديدة <<<---
                            budget_mode=hh_budget_mode,
                            llh_time_budget=hh_time_budget,
                            llh_iterations=hh_llh_iterations,
                            stagnation_limit=hh_stagnation_limit
                        )
                        failures.extend(detailed_failures)
                    except StopByUserException:
                        failures.append({"course_name": "N/A", "teacher_name": "Algorithm", "reason": "تم إيقاف العملية من قبل المستخدم."})
                        final_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                
                elif method == 'greedy':
                    # --- إعداد حلقة التكرار لاختيار أفضل نتيجة ---
                    best_result = {
                        "schedule": {level: [[[] for _ in slots] for _ in days] for level in all_levels},
                        "failures": [],
                        "unplaced_count": float('inf')
                    }
                    num_of_runs = 10  # يمكنك زيادة هذا الرقم للحصول على نتائج أفضل
                    log_q.put(f"--- بدء الخوارزمية الطماعة (سيتم تشغيلها {num_of_runs} مرات لاختيار الأفضل) ---")
                    

                    for run in range(num_of_runs):
                        # --- إعادة تهيئة المتغيرات لكل محاولة جديدة ---
                        current_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
                        current_teacher_schedule = {t['name']: set() for t in teachers}
                        current_room_schedule = {r['name']: set() for r in rooms_data}
                        current_failures = []
                        current_unplaced_count = 0

                        # ✅ --- التصحيح الجوهري: استخدام القائمة المرتبة lectures_sorted ---
                        for lecture in lectures_sorted:
                            success, message = find_slot_for_single_lecture(
                                lecture, current_schedule, current_teacher_schedule, current_room_schedule,
                                days, slots, rules_grid, rooms_data,
                                teacher_constraints, globally_unavailable_slots, special_constraints,
                                primary_slots, reserve_slots, identifiers_by_level,
                                prioritize_primary, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule
                            )
                            if not success:
                                current_unplaced_count += 1
                                current_failures.append({
                                    "course_name": lecture.get('name'),
                                    "teacher_name": lecture.get('teacher_name'),
                                    "reason": message
                                })

                        # --- فحص القيود الإضافية لهذه المحاولة ---
                        greedy_validation_failures = validate_teacher_constraints_in_solution(
                            current_teacher_schedule, 
                            special_constraints, 
                            teacher_constraints,
                            lectures_by_teacher_map, 
                            distribution_rule_type,
                            saturday_teachers, 
                            teacher_pairs, 
                            day_to_idx,
                            last_slot_restrictions,
                            num_slots,
                            max_sessions_per_day
                        )
                        current_failures.extend(greedy_validation_failures)

                        
                        log_q.put(f"   - المحاولة {run + 1}/{num_of_runs}: اكتملت مع وجود {current_unplaced_count} مواد ناقصة.")
                        time.sleep(0.05)

                        # --- تحديث أفضل نتيجة إذا كانت المحاولة الحالية أفضل ---
                        if current_unplaced_count < best_result['unplaced_count']:
                            log_q.put(f"   >>> نتيجة أفضل! تم تقليل النقص إلى {current_unplaced_count}.")
                            
                            best_result['unplaced_count'] = current_unplaced_count
                            best_result['schedule'] = copy.deepcopy(current_schedule)
                            best_result['failures'] = copy.deepcopy(current_failures)
                        # إذا كان النقص متساويًا، نختار النتيجة ذات الأخطاء الأقل
                        elif current_unplaced_count == best_result['unplaced_count'] and len(current_failures) < len(best_result['failures']):
                            log_q.put(f"   >>> نتيجة أفضل! نفس النقص ({current_unplaced_count}) لكن بأخطاء أقل.")
                            
                            best_result['schedule'] = copy.deepcopy(current_schedule)
                            best_result['failures'] = copy.deepcopy(current_failures)

                    # --- بعد انتهاء كل المحاولات، نعتمد أفضل نتيجة تم العثور عليها ---
                    final_schedule = best_result['schedule']
                    failures = best_result['failures']
                    
                    total_greedy_cost = len(failures)
                    progress_percentage = max(0, (10 - total_greedy_cost) / 10 * 100)
                    log_q.put(f"PROGRESS:{progress_percentage:.1f}")
                    
                    detailed_failures = failures

                
                # --- يبدأ هذا القسم من هنا ---
                lecture_counts = defaultdict(int)
                for grid in final_schedule.values():
                    for day in grid:
                        for slot_lectures in day:
                            for lec in slot_lectures:
                                if lec.get('teacher_name'):
                                    lecture_counts[lec.get('teacher_name')] += 1
                
                # === بداية التعديل: توحيد قائمة الفشل النهائية ===
                # تستخدم `detailed_failures` للخوارزميات المتقدمة، و`failures` للطماعة
                # هذا التوحيد يضمن أن `total_failures_list` تحتوي دائمًا على القائمة الصحيحة
                total_failures_list = detailed_failures if detailed_failures else failures
                # === نهاية التعديل ===


                current_attempt_cost = len(total_failures_list)
                
                 # --- ✨ بداية الكود المصحح لحساب عدد مواد كل مستوى ---
                level_counts = defaultdict(int)
                # 'courses' هنا هي القائمة الكاملة للمقررات التي تم جلبها
                for course in courses:
                    # المقرر الآن لديه قائمة من المستويات وليس مستوى واحد
                    levels_for_course = course.get('levels', [])
                    # نمر على كل مستوى في القائمة ونزيد العداد الخاص به
                    for level_name in levels_for_course:
                        level_counts[level_name] += 1

                level_counts_list = [{'level': lvl, 'count': cnt} for lvl, cnt in sorted(level_counts.items())]
                # --- نهاية الكود المصحح ---

                all_results.append({
                    "cost": current_attempt_cost,
                    "schedule": copy.deepcopy(final_schedule),
                    "failures": copy.deepcopy(total_failures_list),
                    "burden": sorted(lecture_counts.items(), key=lambda item: item[1], reverse=True),
                    "days": days,
                    "slots": slots
                })

                if intensive_attempts > 1:
                    log_q.put(f"<<< انتهت المحاولة {attempt + 1} مع تكلفة (تعارضات) = {current_attempt_cost} >>>")
                    
            
            # --- نهاية حلقة المحاولات، والآن معالجة النتيجة النهائية ---
            
            if not all_results:
                raise Exception("خطأ: لم يتم إنتاج أي نتائج بعد انتهاء جميع المحاولات.")

            best_result = min(all_results, key=lambda x: x['cost'])
            
             # --- بداية الإضافة الثانية: حساب عدد المواد الموزعة فعليياً لكل مستوى ---
            placed_level_counts = defaultdict(int)
            # best_result['schedule'] يحتوي على الجدول النهائي الأفضل
            if best_result.get('schedule'):
                for level, grid in best_result['schedule'].items():
                    # نحسب مجموع عدد المحاضرات في كل الخانات لهذا المستوى
                    count_for_level = sum(len(slot_lectures) for day in grid for slot_lectures in day)
                    placed_level_counts[level] = count_for_level

            placed_level_counts_list = [{'level': lvl, 'count': cnt} for lvl, cnt in sorted(placed_level_counts.items())]
            # --- نهاية الإضافة الثانية ---

            # ================== بداية الكود الجديد لتحديد المواد المبدلة ==================
            swapped_lecture_ids = set()

            # أولاً، ننشئ خريطة بالإسناد الأصلي لكل مادة من قاعدة البيانات
            initial_teacher_by_id = {course['id']: course.get('teacher_name') for course in courses_original_state}

            # ثانيًا، نمر على الجدول النهائي ونقارن
            final_schedule_grid = best_result.get('schedule', {})
            for level_grid in final_schedule_grid.values():
                for day in level_grid:
                    for slot in day:
                        for lecture in slot:
                            lec_id = lecture.get('id')
                            initial_teacher = initial_teacher_by_id.get(lec_id)
                            final_teacher = lecture.get('teacher_name')
                            
                            # نعتبر المادة مبدلة فقط إذا كان لها أستاذ ابتدائي ويختلف عن النهائي
                            if initial_teacher and final_teacher and initial_teacher != final_teacher:
                                swapped_lecture_ids.add(lec_id)
            # ================== نهاية الكود الجديد ==================
            
            if intensive_attempts > 1:
                log_q.put(f"\n--- أفضل نتيجة تم العثور عليها بعد {intensive_attempts} محاولات هي بتكلفة {best_result['cost']} تعارضات ---")
                

            unassigned_courses = [c for c in courses if not c.get('teacher_name')]
            
            final_result = {
                "schedule": best_result['schedule'], 
                "days": best_result['days'], 
                "slots": best_result['slots'], 
                "failures": best_result['failures'], 
                "burden_stats": best_result['burden'], 
                "unassigned_courses": unassigned_courses,
                "level_counts": level_counts_list,
                "swapped_lecture_ids": list(swapped_lecture_ids),
                "placed_level_counts": placed_level_counts_list
            }
            log_q.put("DONE" + json.dumps(final_result, ensure_ascii=False))

        except StopByUserException:
            log_q.put('\n--- تم إيقاف العملية بنجاح من قبل المستخدم. ---')
            
            
        except Exception as e:
            traceback.print_exc()
            log_q.put(f'\nحدث خطأ فادح وأوقف العملية: {str(e)}')
            
            
        finally:
            # التأكد من إعادة تعيين الحالة دائماً بعد انتهاء المهمة
            scheduling_state['should_stop'] = False
            
    # ------ بداية منطق الاستدعاء من خارج المهمة الخلفية ------
    SCHEDULING_STATE['should_stop'] = False
    settings = request.get_json()
    courses = get_courses().get_json()
    rooms_data = get_rooms().get_json()
    all_levels = get_levels().get_json()
    teachers = get_teachers().get_json()
    identifiers_row = query_db('SELECT value FROM settings WHERE key = ?', ('non_repetition_identifiers',), one=True)
    identifiers_by_level = json.loads(identifiers_row['value']) if identifiers_row and identifiers_row.get('value') else {}
    
    # Note that we are passing log_queue instead of socketio now
    executor.submit(
        run_scheduling_task, 
        settings, 
        courses, 
        rooms_data, 
        all_levels, 
        teachers, 
        identifiers_by_level,
        SCHEDULING_STATE,
        log_queue # Add log_queue as an argument
    )
    return jsonify({"status": "ok", "message": "بدأت عملية إنشاء الجدول..."})

    

def find_available_room(day_idx, slot_idx, room_schedule, rooms_data, allowed_room_types, specific_hall=None):
    if specific_hall:
        if (day_idx, slot_idx) not in room_schedule.get(specific_hall, set()):
            for room in rooms_data:
                if room.get('name') == specific_hall and room.get('type') in allowed_room_types:
                    return specific_hall
        return None
    potential_rooms = [room for room in rooms_data if room.get("type") in allowed_room_types]
    random.shuffle(potential_rooms)
    for room in potential_rooms:
        room_name = room.get('name')
        if (day_idx, slot_idx) not in room_schedule.get(room_name, set()):
            return room_name
    return None

def calculate_lecture_difficulty(lecture, all_lectures_for_teacher, special_constraints, manual_days):
    """
    تحسب درجة الصعوبة لمحاضرة معينة بناءً على عدة عوامل.
    كلما زادت النقاط، كانت المحاضرة أصعب وتحتاج لأولوية أعلى.
    """
    score = 0
    teacher_name = lecture.get('teacher_name')

    # 1. نقاط للأساتذة ذوي الأيام المحددة يدوياً (أعلى أولوية)
    if teacher_name in manual_days:
        score += 1000

    # 2. نقاط لنوع القاعة (المورد النادر)
    if lecture.get('room_type') == 'كبيرة':
        score += 100

    # 3. نقاط لعبء الأستاذ
    # الأستاذ الذي لديه محاضرات أكثر يحصل على نقاط أعلى
    score += len(all_lectures_for_teacher) * 5

    # 4. نقاط لنوع قاعدة التوزيع
    prof_constraints = special_constraints.get(teacher_name, {})
    distribution_rule = prof_constraints.get('distribution_rule', 'غير محدد')
    difficulty_order = {
        'يومان متتاليان': 50,
        'ثلاثة أيام متتالية': 50,
        'يومان منفصلان': 40,
        'ثلاثة ايام منفصلة': 40,
        'غير محدد': 0
    }
    score += difficulty_order.get(distribution_rule, 0)

    # 5. نقاط للقيود اليدوية الأخرى (البدء والإنهاء)
    if prof_constraints.get('start_d1_s2') or prof_constraints.get('start_d1_s3'):
        score += 15
    if prof_constraints.get('end_s3') or prof_constraints.get('end_s4'):
        score += 15

    return score


# ✨✨✨ النسخة النهائية والصحيحة - استبدل الدالة بالكامل بهذه ✨✨✨

def is_placement_valid(lecture, day_idx, slot_idx, final_schedule, teacher_schedule, room_schedule, teacher_constraints, special_constraints, identifiers_by_level, rules_grid, globally_unavailable_slots, rooms_data, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule):
    teacher = lecture.get('teacher_name')
    levels_for_lecture = lecture.get('levels', [])
    lecture_room_type_needed = lecture.get('room_type')

    # --- 1. التحقق من القيود العامة (التي لا تعتمد على المستوى) ---
    if (day_idx, slot_idx) in globally_unavailable_slots or \
       (day_idx, slot_idx) in teacher_schedule.get(teacher, set()):
        return False, "Slot unavailable for teacher or general rest period"

    saturday_idx = day_to_idx.get('السبت', -1)
    if saturday_idx != -1 and saturday_teachers and day_idx == saturday_idx and teacher not in saturday_teachers:
        return False, "الأستاذ غير مسموح له بالعمل يوم السبت"

    prof_manual_days_indices = teacher_constraints.get(teacher, {}).get('allowed_days')
    if prof_manual_days_indices:
        if day_idx not in prof_manual_days_indices: 
            return False, "Manual day constraint violation"
        # (بقية القيود اليدوية للأستاذ)
        prof_special_constraints = special_constraints.get(teacher, {})
        first_manual_day_idx, last_manual_day_idx = min(prof_manual_days_indices), max(prof_manual_days_indices)
        if day_idx == first_manual_day_idx and ((prof_special_constraints.get('start_d1_s2') and slot_idx < 1) or (prof_special_constraints.get('start_d1_s3') and slot_idx < 2)):
            return False, "Manual start time violation"
        if day_idx == last_manual_day_idx and ((prof_special_constraints.get('end_s3') and slot_idx > 2) or (prof_special_constraints.get('end_s4') and slot_idx > 3)):
            return False, "Manual end time violation"

    # --- 2. تجميع القيود من جميع المستويات المشاركة ---
    required_halls_from_all_levels = set()
    allowed_types_per_level_list = []

    for level in levels_for_lecture:
        # أ. التحقق من التعارضات الفورية داخل كل مستوى
        lectures_in_slot = final_schedule[level][day_idx][slot_idx]
        if lectures_in_slot and (lecture_room_type_needed == 'كبيرة' or any(l.get('room_type') == 'كبيرة' for l in lectures_in_slot)):
            return False, f"Large room conflict in level {level}"
        
        identifiers_for_level = identifiers_by_level.get(level, [])
        current_identifier = get_contained_identifier(lecture['name'], identifiers_for_level)
        if current_identifier:
            used_identifiers = {get_contained_identifier(l['name'], identifiers_for_level) for l in lectures_in_slot}
            if current_identifier in used_identifiers:
                return False, f"Identifier conflict in level {level}"

        # ب. تجميع متطلبات القاعات المحددة
        course_full_name = f"{lecture.get('name')} ({level})"
        if room := specific_small_room_assignments.get(course_full_name):
            required_halls_from_all_levels.add(room)
        if lecture_room_type_needed == 'كبيرة':
            if room := level_specific_large_rooms.get(level):
                required_halls_from_all_levels.add(room)

        # ج. تجميع أنواع القاعات المسموحة حسب قواعد الفترة الزمنية
        rules_for_slot = rules_grid[day_idx][slot_idx]
        level_specific_rules = [r for r in rules_for_slot if level in r.get('levels', [])]

        # --- بداية الإضافة: التحقق من قيد المنع أولاً ---
        if any(r.get('rule_type') == 'NO_HALLS_ALLOWED' for r in level_specific_rules):
            return False, f"Level {level} is explicitly forbidden in this slot by a 'NO_HALLS_ALLOWED' rule."
        # --- نهاية الإضافة ---
        
        if not level_specific_rules:
            # إذا لم توجد قاعدة لهذا المستوى، فكل الأنواع مسموحة له
            allowed_types_per_level_list.append({'كبيرة', 'صغيرة'})
        else:
            current_level_allowed_types = set()
            for rule in level_specific_rules:
                rule_type = rule.get('rule_type')
                if rule_type == 'ANY_HALL': current_level_allowed_types.update(['كبيرة', 'صغيرة'])
                elif rule_type == 'SMALL_HALLS_ONLY': current_level_allowed_types.add('صغيرة')
                elif rule_type == 'SPECIFIC_LARGE_HALL':
                    current_level_allowed_types.add('كبيرة')
                    if hall := rule.get('hall_name'):
                        required_halls_from_all_levels.add(hall)
            allowed_types_per_level_list.append(current_level_allowed_types)

    # --- 3. التحقق النهائي من القيود المجمعة ---
    if len(required_halls_from_all_levels) > 1:
        return False, f"Impossible placement: Conflicting specific hall requirements {required_halls_from_all_levels}"

    # إيجاد تقاطع جميع مجموعات أنواع القاعات المسموحة
    final_allowed_types = set.intersection(*allowed_types_per_level_list) if allowed_types_per_level_list else set()

    if lecture_room_type_needed not in final_allowed_types:
        return False, f"Room type '{lecture_room_type_needed}' is not allowed by the combined slot rules."

    # --- 4. إيجاد قاعة متاحة ---
    final_specific_hall = required_halls_from_all_levels.pop() if required_halls_from_all_levels else None
    available_room = find_available_room(day_idx, slot_idx, room_schedule, rooms_data, [lecture_room_type_needed], final_specific_hall)
    
    if not available_room:
        return False, "No available room that satisfies all constraints"

    # --- بداية المنطق الجديد: التحقق من قيد التوالي في القاعات الكبيرة ---
    rule = consecutive_large_hall_rule # سيكون الآن "none", "all", أو اسم قاعة
    if rule != 'none' and lecture_room_type_needed == 'كبيرة' and slot_idx > 0:
        for level in levels_for_lecture:
            previous_slot_lectures = final_schedule.get(level, [[]] * (slot_idx + 1))[day_idx][slot_idx - 1]
            for prev_lec in previous_slot_lectures:
                # التحقق إذا كانت القاعدة للكل أو لقاعة محددة تطابق القاعة الحالية
                if prev_lec.get('room') == available_room and (rule == 'all' or rule == available_room):
                    return False, f"Consecutive large hall violation for room {available_room}"
    # --- نهاية المنطق الجديد ---
    
    return True, available_room


# النسخة النهائية والشاملة للدالة
def calculate_slot_fitness(teacher_name, day_idx, slot_idx, teacher_schedule, special_constraints):
    """
    تحسب جودة الخانة مع مكافآت وعقوبات لكل القيود المرنة.
    """
    fitness = 100  # درجة أساسية
    teacher_slots = teacher_schedule.get(teacher_name, set())
    prof_constraints = special_constraints.get(teacher_name, {})

    # 1. مكافأة لوضع المحاضرات في نفس اليوم (للتجميع)
    slots_on_same_day = {s for d, s in teacher_slots if d == day_idx}
    if slots_on_same_day:
        fitness += 50
        is_adjacent = any(abs(slot_idx - existing_slot_idx) == 1 for existing_slot_idx in slots_on_same_day)
        if is_adjacent:
            fitness += 150

    # 2. مكافأة للأيام المتتالية (إذا طُلب ذلك)
    distribution_rule = prof_constraints.get('distribution_rule', 'غير محدد')
    if 'متتاليان' in distribution_rule or 'متتالية' in distribution_rule:
        worked_days = {d for d, s in teacher_slots}
        if worked_days:
            is_adjacent_day = any(abs(day_idx - worked_day) == 1 for worked_day in worked_days)
            if is_adjacent_day:
                fitness += 200

    # --- بداية الإضافة الجديدة: عقوبة على خرق أوقات البدء/الانتهاء (بشكل مرن) ---
    # 3. نطبق هذه العقوبة فقط في حال عدم تحديد الأيام يدوياً
    # (لأن الحالة اليدوية يتم فرضها كقيد صارم في دالة is_placement_valid)
    
    # عقوبة على البدء المبكر جداً
    if prof_constraints.get('start_d1_s2') and slot_idx < 1:
        fitness -= 100  # عقوبة لوضع محاضرة في الحصة الأولى
    if prof_constraints.get('start_d1_s3') and slot_idx < 2:
        fitness -= 100  # عقوبة إضافية لوضعها في الحصة الأولى أو الثانية

    # عقوبة على الإنهاء المتأخر جداً
    if prof_constraints.get('end_s3') and slot_idx > 2:
        fitness -= 100  # عقوبة لوضع محاضرة بعد الحصة الثالثة
    if prof_constraints.get('end_s4') and slot_idx > 3:
        fitness -= 100  # عقوبة إضافية لوضعها بعد الحصة الرابعة
    # --- نهاية الإضافة الجديدة ---
            
    return fitness

# ================== بداية الكود الجديد ==================
# هذه الدالة الجديدة ستحل محل دالتي التحقق من التوزيع القديمتين
# ================== بداية الكود الجديد المقترح ==================

# النسخة النهائية والصحيحة لدالة التحقق من القيود
def validate_teacher_constraints_in_solution(teacher_schedule, special_constraints, teacher_constraints, lectures_by_teacher_map, distribution_rule_type, saturday_teachers, teacher_pairs, day_to_idx, last_slot_restrictions, num_slots, max_sessions_per_day=None):
    failures = []
    
    # ✨ --- بداية الجزء الجديد: التحقق من خرق الأيام المحددة يدويًا --- ✨
    for teacher_name, constraints in teacher_constraints.items():
        if 'allowed_days' in constraints:
            allowed_days_set = constraints['allowed_days']
            assigned_slots = teacher_schedule.get(teacher_name, set())
            for day_idx, slot_idx in assigned_slots:
                if day_idx not in allowed_days_set:
                    failures.append({
                        "course_name": "قيد الأيام اليدوية", "teacher_name": teacher_name,
                        "reason": "الأستاذ يعمل في يوم غير مسموح به يدويًا."
                    })
                    break # نكتفي بخطأ واحد لكل أستاذ لتجنب التكرار
    # ✨ --- نهاية الجزء الجديد ---
    
    failures.extend(validate_start_end_times(teacher_schedule, special_constraints, teacher_constraints))

    saturday_idx = day_to_idx.get('السبت', -1)
    if saturday_idx != -1 and saturday_teachers:
        for teacher_name, slots in teacher_schedule.items():
            if teacher_name not in saturday_teachers and any(day == saturday_idx for day, _ in slots):
                failures.append({
                    "course_name": "قيد السبت", "teacher_name": teacher_name,
                    "reason": "الأستاذ لا يجب أن يعمل يوم السبت."
                })

    # --- بداية الكود الجديد: التحقق من قيد آخر حصص ---
    if num_slots > 0 and last_slot_restrictions:
        for teacher_name, restriction in last_slot_restrictions.items():
            teacher_slots = teacher_schedule.get(teacher_name, set())
            if not teacher_slots:
                continue

            restricted_indices = []
            if restriction == 'last_1' and num_slots >= 1:
                restricted_indices.append(num_slots - 1)
            elif restriction == 'last_2' and num_slots >= 2:
                restricted_indices.append(num_slots - 1)
                restricted_indices.append(num_slots - 2)

            if any(slot_idx in restricted_indices for day_idx, slot_idx in teacher_slots):
                failures.append({
                    "course_name": f"قيد آخر {len(restricted_indices)} حصص",
                    "teacher_name": teacher_name,
                    "reason": f"الأستاذ لا يجب أن يعمل في آخر {len(restricted_indices)} حصص من اليوم."
                })
    # --- نهاية الكود الجديد ---

    if max_sessions_per_day:
        for teacher_name, slots in teacher_schedule.items():

            sessions_per_day = defaultdict(int)
            for day_idx, _ in slots:
                sessions_per_day[day_idx] += 1

            for day_idx, count in sessions_per_day.items():
                if count > max_sessions_per_day:
                    failures.append({
                        "course_name": "قيد الحصص اليومية",
                        "teacher_name": teacher_name,
                        "reason": f"تجاوز الحد الأقصى للحصص في اليوم ({count} > {max_sessions_per_day})."
                    })

    if teacher_pairs:
        teacher_work_days = {t: {d for d, s in sl} for t, sl in teacher_schedule.items()}
        for t1, t2 in teacher_pairs:
            days1, days2 = teacher_work_days.get(t1, set()), teacher_work_days.get(t2, set())
            if days1 != days2:
                failures.append({
                    "course_name": "قيد الأزواج", "teacher_name": f"{t1} و {t2}",
                    "reason": "أيام عمل الأستاذين غير متطابقة."
                })

    for teacher_name, prof_constraints in special_constraints.items():
        if teacher_constraints.get(teacher_name, {}).get('allowed_days'): continue
        rule = prof_constraints.get('distribution_rule', 'غير محدد')
        if rule == 'غير محدد': continue

        assigned_slots = teacher_schedule.get(teacher_name, set())
        if not assigned_slots: continue

        day_indices = sorted(list({d for d, s in assigned_slots}))
        num_days = len(day_indices)
        target_days = 0
        if 'يومان' in rule or 'يومين' in rule: target_days = 2
        elif 'ثلاثة أيام' in rule or '3 أيام' in rule: target_days = 3
        if target_days == 0: continue

        if distribution_rule_type == 'required' and num_days != target_days:
            failures.append({
                "course_name": "قيد التوزيع (صارم)", "teacher_name": teacher_name,
                "reason": f"يجب أن يعمل {target_days} أيام بالضبط (يعمل حالياً {num_days})."
            })
        elif distribution_rule_type == 'allowed' and num_days > target_days:
            failures.append({
                "course_name": "قيد التوزيع (مرن)", "teacher_name": teacher_name,
                "reason": f"يجب أن يعمل {target_days} أيام كحد أقصى (يعمل حالياً {num_days})."
            })

        if 'متتاليان' in rule or 'متتالية' in rule:
            if num_days > 1 and any(day_indices[i+1] - day_indices[i] != 1 for i in range(num_days - 1)):
                failures.append({
                    "course_name": "قيد التوزيع", "teacher_name": teacher_name,
                    "reason": "أيام عمل الأستاذ ليست متتالية كما هو مطلوب."
                })

    return failures




# ابحث عن هذه الدالة واستبدلها بالكامل
def validate_start_end_times(teacher_schedule, special_constraints, teacher_constraints):
    failures = []
    for teacher_name, prof_constraints in special_constraints.items():
        if prof_constraints.get('always_s2_to_s4'):
            assigned_slots = teacher_schedule.get(teacher_name, set())
            if not assigned_slots: continue
            slots_by_day = defaultdict(list)
            for day, slot in assigned_slots: slots_by_day[day].append(slot)
            for day, slots in slots_by_day.items():
                min_slot, max_slot = min(slots), max(slots)
                if min_slot < 1:
                    failures.append({"course_name": "قيد وقت البدء", "teacher_name": teacher_name, "reason": "قيد (كل الأيام): بدأ قبل الحصة الثانية."})
                if max_slot > 3:
                    failures.append({"course_name": "قيد وقت الإنهاء", "teacher_name": teacher_name, "reason": "قيد (كل الأيام): انتهى بعد الحصة الرابعة."})
            continue

        has_start_end = any(k in prof_constraints for k in ['start_d1_s2', 'start_d1_s3', 'end_s3', 'end_s4'])
        if not has_start_end: continue

        assigned_slots = teacher_schedule.get(teacher_name, set())
        if not assigned_slots: continue
        
        day_indices = {d for d, s in assigned_slots}
        if not day_indices: continue
        
        manual_days = teacher_constraints.get(teacher_name, {}).get('allowed_days')
        first_day, last_day = (min(manual_days), max(manual_days)) if manual_days else (min(day_indices), max(day_indices))

        for day, slot in assigned_slots:
            if day == first_day:
                if prof_constraints.get('start_d1_s2') and slot < 1:
                    failures.append({"course_name": "قيد وقت البدء", "teacher_name": teacher_name, "reason": "بدأ قبل الحصة الثانية في اليوم الأول."})
                if prof_constraints.get('start_d1_s3') and slot < 2:
                    failures.append({"course_name": "قيد وقت البدء", "teacher_name": teacher_name, "reason": "بدأ قبل الحصة الثالثة في اليوم الأول."})
            if day == last_day:
                if prof_constraints.get('end_s3') and slot > 2:
                    failures.append({"course_name": "قيد وقت الإنهاء", "teacher_name": teacher_name, "reason": "انتهى بعد الحصة الثالثة في اليوم الأخير."})
                if prof_constraints.get('end_s4') and slot > 3:
                    failures.append({"course_name": "قيد وقت الإنهاء", "teacher_name": teacher_name, "reason": "انتهى بعد الحصة الرابعة في اليوم الأخير."})
    return failures

# ================== نهاية الكود الجديد المقترح ==================

# --- واجهات الحذف والتعديل (محولة إلى SQLite) ---
@app.route('/api/levels', methods=['DELETE'])
def delete_level():
    data = request.get_json()
    level_to_delete = data.get('level')
    if not level_to_delete:
        return jsonify({"error": "اسم المستوى مفقود"}), 400
    
    # التحقق من أن المستوى غير مستخدم في أي مقرر
    courses_using_level = query_db('''
        SELECT 1 FROM courses c JOIN levels l ON c.level_id = l.id 
        WHERE l.name = ? LIMIT 1
    ''', (level_to_delete,), one=True)
    
    if courses_using_level:
        return jsonify({"error": f"لا يمكن حذف المستوى '{level_to_delete}' لأنه مستخدم في بعض المقررات."}), 409
    
    execute_db('DELETE FROM levels WHERE name = ?', (level_to_delete,))
    return jsonify({"success": True, "message": "تم حذف المستوى."})

@app.route('/api/levels/edit', methods=['POST'])
def edit_level():
    data = request.get_json()
    old_level, new_level = data.get('old_level'), data.get('new_level')
    if not old_level or not new_level: return jsonify({"error": "البيانات غير كافية"}), 400
    
    try:
        # UNIQUE constraint سيمنع التكرار
        execute_db('UPDATE levels SET name = ? WHERE name = ?', (new_level, old_level))
    except sqlite3.IntegrityError:
        return jsonify({"error": f"المستوى '{new_level}' موجود بالفعل."}), 409
        
    return jsonify({"success": True, "message": "تم تعديل المستوى بنجاح."})

@app.route('/api/students/bulk', methods=['POST'])
def bulk_add_students():
    new_courses = request.get_json()
    if not isinstance(new_courses, list): return jsonify({"error": "تنسيق البيانات غير صالح."}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # ✨ جلب كل المستويات مرة واحدة لتحسين الأداء
    level_map = {row['name']: row['id'] for row in query_db('SELECT id, name FROM levels')}

    for course in new_courses:
        course_name = course.get('name')
        room_type = course.get('room_type')
        # ✨ الآن level هو قائمة من الأسماء
        levels_for_course = course.get('levels', [])

        if not all([course_name, room_type, levels_for_course]):
            continue # تجاهل الإدخالات غير المكتملة

        # 1. إضافة المقرر إلى جدول courses والحصول على الـ ID الخاص به
        cursor.execute(
            'INSERT INTO courses (name, room_type) VALUES (?, ?)',
            (course_name, room_type)
        )
        course_id = cursor.lastrowid

        # 2. ربط المقرر بكل المستويات المحددة في جدول course_levels
        for level_name in levels_for_course:
            if level_name in level_map:
                level_id = level_map[level_name]
                cursor.execute(
                    'INSERT OR IGNORE INTO course_levels (course_id, level_id) VALUES (?, ?)',
                    (course_id, level_id)
                )

    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": f"تمت إضافة {len(new_courses)} مقررات بنجاح."})

@app.route('/api/teachers', methods=['POST'])
def add_teacher():
    teacher_names = request.json.get('names', [])
    if not teacher_names: return jsonify({"error": "قائمة الأسماء مفقودة"}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    for name in teacher_names:
        if name:
            cursor.execute('INSERT OR IGNORE INTO teachers (name) VALUES (?)', (name,))
    conn.commit()
    
    return jsonify(query_db('SELECT id, name FROM teachers')), 201

@app.route('/api/rooms', methods=['POST'])
def add_room():
    data = request.get_json()
    room_names = data.get('names', [])
    room_type = data.get('type')
    if not room_names or not room_type: return jsonify({"error": "بيانات القاعات غير مكتملة"}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    for name in room_names:
        if name:
            cursor.execute('INSERT OR IGNORE INTO rooms (name, type) VALUES (?, ?)', (name, room_type))
    conn.commit()
    
    return jsonify(query_db('SELECT id, name, type FROM rooms')), 201

@app.route('/api/assign-courses/bulk', methods=['POST'])
def bulk_assign_courses_to_teacher():
    data = request.get_json()
    teacher_name = data.get('teacher')
    course_ids = data.get('course_ids') # ✨ استقبال قائمة الـ IDs

    if not teacher_name or not isinstance(course_ids, list): 
        return jsonify({"error": "بيانات غير صالحة."}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()

    # الحصول على ID الأستاذ
    teacher_row = cursor.execute('SELECT id FROM teachers WHERE name = ?', (teacher_name,)).fetchone()
    if not teacher_row:
        conn.close()
        return jsonify({"error": "لم يتم العثور على الأستاذ."}), 404
    teacher_id = teacher_row['id']

    # ✨ حلقة بسيطة لتحديث كل مقرر باستخدام الـ ID الخاص به
    for course_id in course_ids:
        cursor.execute(
            'UPDATE courses SET teacher_id = ? WHERE id = ?',
            (teacher_id, course_id)
        )

    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": f"تم تخصيص {len(course_ids)} مقررات للأستاذ {teacher_name}."})

@app.route('/api/unassign-course', methods=['POST'])
def unassign_course():
    data = request.get_json()
    course_id = data.get('course_id') 
    if not course_id: return jsonify({"error": "معرّف المقرر مفقود"}), 400
    
    execute_db('UPDATE courses SET teacher_id = NULL WHERE id = ?', (course_id,))
    return jsonify({"success": True, "message": "تم إلغاء تخصيص المقرر بنجاح."})

@app.route('/api/teachers', methods=['DELETE'])
def delete_teacher():
    data = request.get_json()
    name_to_delete = data.get('name')
    if not name_to_delete: return jsonify({"error": "اسم الأستاذ مفقود"}), 400
    
    courses_using_teacher = query_db('''
        SELECT 1 FROM courses c JOIN teachers t ON c.teacher_id = t.id 
        WHERE t.name = ? LIMIT 1
    ''', (name_to_delete,), one=True)
    
    if courses_using_teacher:
        return jsonify({"error": f"لا يمكن حذف الأستاذ '{name_to_delete}' لأنه مسند إليه مقررات."}), 409
        
    execute_db('DELETE FROM teachers WHERE name = ?', (name_to_delete,))
    return jsonify({"success": True, "message": "تم حذف الأستاذ."})

@app.route('/api/teachers/edit', methods=['POST'])
def edit_teacher():
    data = request.get_json()
    old_name, new_name = data.get('old_name'), data.get('new_name')
    if not old_name or not new_name: return jsonify({"error": "البيانات غير كافية"}), 400
    try:
        execute_db('UPDATE teachers SET name = ? WHERE name = ?', (new_name, old_name))
    except sqlite3.IntegrityError:
        return jsonify({"error": f"الاسم الجديد '{new_name}' موجود بالفعل."}), 409
    return jsonify({"success": True, "message": "تم تعديل اسم الأستاذ بنجاح."})

@app.route('/api/rooms', methods=['DELETE'])
def delete_room():
    data = request.get_json()
    name_to_delete = data.get('name')
    if not name_to_delete: return jsonify({"error": "اسم القاعة مفقود"}), 400
    execute_db('DELETE FROM rooms WHERE name = ?', (name_to_delete,))
    return jsonify({"success": True, "message": "تم حذف القاعة."})

@app.route('/api/rooms/edit', methods=['POST'])
def edit_room():
    data = request.get_json()
    old_name, new_name, new_type = data.get('old_name'), data.get('new_name'), data.get('new_type')
    if not all([old_name, new_name, new_type]): return jsonify({"error": "البيانات غير كافية"}), 400
    try:
        execute_db('UPDATE rooms SET name = ?, type = ? WHERE name = ?', (new_name, new_type, old_name))
    except sqlite3.IntegrityError:
        return jsonify({"error": f"الاسم الجديد '{new_name}' موجود بالفعل."}), 409
    return jsonify({"success": True, "message": "تم تعديل القاعة بنجاح."})

@app.route('/api/students', methods=['DELETE'])
def delete_course():
    data = request.get_json()
    course_id = data.get('id') # ✨ الاعتماد على الـ ID للحذف
    if not course_id: return jsonify({"error": "معرّف المقرر مفقود"}), 400
    
    execute_db('DELETE FROM courses WHERE id = ?', (course_id,))
    return jsonify({"success": True, "message": "تم حذف المقرر."})

@app.route('/api/students/edit', methods=['POST'])
def edit_course():
    data = request.get_json()
    course_id = data.get('id')
    new_name = data.get('new_name')
    new_room_type = data.get('new_room_type')
    new_levels_names = data.get('new_levels', []) # ✨ استقبال قائمة المستويات الجديدة

    if not all([course_id, new_name, new_room_type, new_levels_names]): 
        return jsonify({"error": "البيانات غير كافية"}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. تحديث البيانات الأساسية للمقرر في جدول courses
    cursor.execute(
        'UPDATE courses SET name = ?, room_type = ? WHERE id = ?',
        (new_name, new_room_type, course_id)
    )

    # 2. حذف جميع الروابط القديمة للمقرر مع المستويات
    cursor.execute('DELETE FROM course_levels WHERE course_id = ?', (course_id,))

    # 3. إنشاء الروابط الجديدة مع المستويات المحددة
    level_map = {row['name']: row['id'] for row in query_db('SELECT id, name FROM levels')}
    for level_name in new_levels_names:
        if level_name in level_map:
            level_id = level_map[level_name]
            cursor.execute(
                'INSERT INTO course_levels (course_id, level_id) VALUES (?, ?)',
                (course_id, level_id)
            )
            
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "تم تعديل المقرر بنجاح."})

# --- دوال التصدير والتقارير (تبقى كما هي) ---
@app.route('/api/schedules/by-professor', methods=['POST'])
def get_schedules_by_professor():
    # هذه الدالة لا تتعامل مباشرة مع قاعدة البيانات، بل مع نتيجة الجدولة
    data = request.get_json()
    schedule_by_level, days, slots = data.get("schedule"), data.get("days", []), data.get("slots", [])
    if not all([schedule_by_level, days, slots]): return jsonify({"error": "بيانات غير كافية"}), 400
    schedules_by_teacher = {}
    for level, grid in schedule_by_level.items():
        for day_idx in range(len(days)):
            for slot_idx in range(len(slots)):
                if day_idx < len(grid) and slot_idx < len(grid[day_idx]):
                    for lecture in grid[day_idx][slot_idx]:
                        teacher_name = lecture.get('teacher_name')
                        if teacher_name:
                            if teacher_name not in schedules_by_teacher:
                                schedules_by_teacher[teacher_name] = [[[] for _ in slots] for _ in days]
                            lecture_with_level = {**lecture, 'level': level}
                            schedules_by_teacher[teacher_name][day_idx][slot_idx].append(lecture_with_level)
    return jsonify(schedules_by_teacher)

@app.route('/api/schedules/free-rooms', methods=['POST'])
def get_free_rooms_schedule():
    data = request.get_json()
    schedule_by_level, days, slots = data.get("schedule"), data.get("days", []), data.get("slots", [])
    if not all([schedule_by_level, days, slots]): return jsonify({"error": "بيانات غير كافية"}), 400
    rooms_data = get_rooms().get_json()
    all_room_names = {room['name'] for room in rooms_data}
    occupied_rooms_by_slot = [[set() for _ in slots] for _ in days]
    for grid in schedule_by_level.values():
        for day_idx in range(len(days)):
            for slot_idx in range(len(slots)):
                if day_idx < len(grid) and slot_idx < len(grid[day_idx]):
                    for lecture in grid[day_idx][slot_idx]:
                        if lecture.get('room'): occupied_rooms_by_slot[day_idx][slot_idx].add(lecture.get('room'))
    free_rooms_schedule = [[sorted(list(all_room_names - occupied_rooms_by_slot[d][s])) for s in range(len(slots))] for d in range(len(days))]
    return jsonify(free_rooms_schedule)

@app.route('/api/validate-data', methods=['GET'])
def validate_data():
    courses = get_courses().get_json()
    warnings = []
    lectures_by_teacher = {}
    for lec in [c for c in courses if c.get('teacher_name')]:
        teacher = lec.get('teacher_name')
        lectures_by_teacher.setdefault(teacher, 0)
        lectures_by_teacher[teacher] += 1
    for teacher, count in lectures_by_teacher.items():
        if count > 15: warnings.append(f"تحذير: الأستاذ {teacher} لديه {count} محاضرة، قد يكون من الصعب جدولته بالقيود الحالية.")
    return jsonify(warnings)

def process_and_format_sheet(writer, df, sheet_name, title=None, sheet_type=None):
    # تحديد صف البداية
    start_row = 0
    if title:
        start_row = 2

    # كتابة البيانات إلى الورقة أولاً
    df.to_excel(writer, sheet_name=sheet_name, index=True, index_label='الوقت', startrow=start_row)
    
    workbook  = writer.book
    worksheet = writer.sheets[sheet_name]
    worksheet.right_to_left()
    worksheet.hide_gridlines(2)

    # 1. تعريف تنسيق لتحديد محاذاة وعرض الأعمدة (بدون حدود)
    column_setup_format = workbook.add_format({
        'text_wrap': True,
        'valign': 'vcenter',
        'align': 'center',
    })

    # 2. تطبيق التنسيق أعلاه على الأعمدة
    if sheet_type == 'professor':
        worksheet.set_column(1, len(df.columns), 22, column_setup_format)
    else:
        worksheet.set_column(1, len(df.columns), 29, column_setup_format)
    worksheet.set_column(0, 0, 15, column_setup_format)

    # 3. تعريف تنسيق للحدود فقط
    border_format = workbook.add_format({'border': 1})

    # 4. تحديد نطاق البيانات وتطبيق تنسيق الحدود عليه
    data_rows = len(df.index)
    data_cols = len(df.columns)
    
    # <<< بداية التعديل >>>
    # تطبيق الحدود على الخلايا التي تحتوي على بيانات
    worksheet.conditional_format(start_row, 0, start_row + data_rows, data_cols, 
                                 {'type': 'no_blanks', 'format': border_format})
    # تطبيق الحدود على الخلايا الفارغة داخل نفس النطاق أيضاً
    worksheet.conditional_format(start_row, 0, start_row + data_rows, data_cols, 
                                 {'type': 'blanks', 'format': border_format})
    # <<< نهاية التعديل >>>

    # تطبيق تنسيق العنوان والترويسات
    if title:
        title_format = workbook.add_format({
            'bold': True, 'font_size': 16, 'align': 'center', 'valign': 'vcenter',
            'fg_color': '#4F81BD', 'font_color': 'white', 'border': 1
        })
        worksheet.merge_range(0, 0, 0, data_cols, title, title_format)
        worksheet.set_row(0, 30)

    header_format = workbook.add_format({
        'bold': True, 'text_wrap': True, 'valign': 'vcenter', 'align': 'center',
        'fg_color': '#D7E4BC', 'border': 1
    })
    for col_num, value in enumerate(df.columns.values):
        worksheet.write(start_row, col_num + 1, value, header_format)
    worksheet.write(start_row, 0, df.index.name, header_format)

@app.route('/api/export/all-levels', methods=['POST'])
def export_all_levels():
    # ... (This function and the ones below remain unchanged as they process data in memory)
    data = request.get_json()
    schedules_by_level, days, slots = data.get('schedule'), data.get('days', []), data.get('slots', [])
    if not all([schedules_by_level, days, slots]): return jsonify({"error": "بيانات التصدير غير كاملة"}), 400
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        level_name_map = {"Bachelor 1": "ليسانس 1", "Bachelor 2": "ليسانس 2", "Bachelor 3": "ليسانس 3", "Master 1": "ماستر 1", "Master 2": "ماستر 2"}
        for level, grid_data in schedules_by_level.items():
            processed_data = [["\n\n".join([f"{lec.get('name', '')}\n{lec.get('teacher_name', '')}\n{lec.get('room', '')}".strip() for lec in grid_data[j][i]]) for j in range(len(days))] for i in range(len(slots))]
            df = pd.DataFrame(processed_data, index=slots, columns=days)
            sheet_name = level_name_map.get(level, level)
            process_and_format_sheet(writer, df, sheet_name, title=sheet_name, sheet_type='level')
    output.seek(0)
    # --- السطر التالي هو الذي تم تعديله ---
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='جداول المستويات.xlsx')

@app.route('/api/export/all-professors', methods=['POST'])
def export_all_professors():
    data = request.get_json()
    schedules_by_prof, days, slots = data.get('schedule'), data.get('days', []), data.get('slots', [])
    if not all([schedules_by_prof, days, slots]): return jsonify({"error": "بيانات التصدير غير كاملة"}), 400
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        level_name_map = {"Bachelor 1": "ليسانس 1", "Bachelor 2": "ليسانس 2", "Bachelor 3": "ليسانس 3", "Master 1": "ماستر 1", "Master 2": "ماستر 2"}
        for prof_name, grid_data in schedules_by_prof.items():
            processed_data = []
            for i in range(len(slots)):
                row_data = []
                for j in range(len(days)):
                    cell_texts = [f"{lec.get('name', '')}\nالمستوى: {level_name_map.get(lec.get('level', ''), lec.get('level', ''))}\n{lec.get('room', '')}".strip() for lec in grid_data[j][i]]
                    row_data.append("\n\n".join(cell_texts))
                processed_data.append(row_data)
            df = pd.DataFrame(processed_data, index=slots, columns=days)
            safe_sheet_name = "".join([c for c in prof_name if c.isalnum() or c.isspace()])[:31]
            process_and_format_sheet(writer, df, safe_sheet_name, title=prof_name, sheet_type='professor')
    output.seek(0)
    # --- السطر التالي هو الذي تم تعديله ---
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='جداول الاساتذة.xlsx')

@app.route('/api/export/free-rooms', methods=['POST'])
def export_free_rooms():
    data = request.get_json()
    free_rooms_grid, days, slots = data.get('schedule'), data.get('days', []), data.get('slots', [])
    if not all([free_rooms_grid, days, slots]): return jsonify({"error": "بيانات التصدير غير كاملة"}), 400
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        processed_data = [["\n".join(free_rooms_grid[j][i]) for j in range(len(days))] for i in range(len(slots))]
        df = pd.DataFrame(processed_data, index=slots, columns=days)
        process_and_format_sheet(writer, df, 'القاعات الشاغرة')
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='جدول_القاعات_الشاغرة.xlsx')

# ================== الجزء الإضافي: النسخ الاحتياطي والاستعادة (محول إلى SQLite) ==================
@app.route('/api/backup', methods=['GET'])
def backup_data():
    """
    يجمع كل البيانات من جداول قاعدة البيانات ويصدرها في ملف JSON واحد.
    """
    # ✨ استعلام جديد ومحدث لجلب المقررات مع قائمة مستوياتها
    courses_with_levels = get_courses().get_json()

    all_data = {
        "levels": [l['name'] for l in query_db('SELECT name FROM levels')],
        "teachers": query_db('SELECT name FROM teachers'),
        "rooms": query_db('SELECT name, type FROM rooms'),
        "courses": courses_with_levels, # ✨ استخدام البيانات الصحيحة هنا
        "settings": json.loads(query_db('SELECT value FROM settings WHERE key = ?', ('main_settings',), one=True).get('value', '{}'))
    }
    
    # لم نعد بحاجة لجلب assigned_courses بشكل منفصل لأنها مدمجة الآن

    json_string = json.dumps(all_data, ensure_ascii=False, indent=4)
    buffer = io.BytesIO(json_string.encode('utf-8'))
    
    return send_file(buffer, as_attachment=True, download_name='schedule_backup.json', mimetype='application/json')

@app.route('/api/restore', methods=['POST'])
def restore_data():
    """
    يستقبل ملف JSON ويقوم بالكتابة فوق البيانات في جداول قاعدة البيانات.
    (نسخة محدثة تدعم النسخ الاحتياطية القديمة والجديدة)
    """
    try:
        backup_data = request.get_json()
        
        # مسح البيانات القديمة
        execute_db('DELETE FROM course_levels')
        execute_db('DELETE FROM courses')
        execute_db('DELETE FROM rooms')
        execute_db('DELETE FROM teachers')
        execute_db('DELETE FROM levels')
        execute_db('DELETE FROM settings')

        conn = get_db_connection()
        cursor = conn.cursor()

        # استعادة البيانات البسيطة
        for level in backup_data.get('levels', []): cursor.execute('INSERT OR IGNORE INTO levels (name) VALUES (?)', (level,))
        for teacher in backup_data.get('teachers', []): cursor.execute('INSERT OR IGNORE INTO teachers (name) VALUES (?)', (teacher['name'],))
        for room in backup_data.get('rooms', []): cursor.execute('INSERT OR IGNORE INTO rooms (name, type) VALUES (?, ?)', (room['name'], room['type']))
        
        conn.commit() # حفظ المستويات والأساتذة أولاً
        
        # ✨ جلب الخرائط اللازمة مرة واحدة
        level_map = {row['name']: row['id'] for row in query_db("SELECT id, name FROM levels")}
        teacher_map = {row['name']: row['id'] for row in query_db("SELECT id, name FROM teachers")}

        # استعادة المقررات (يدعم النسق القديم `students` والجديد `courses`)
        courses_list = backup_data.get('courses', backup_data.get('students', []))
        
        for course in courses_list:
            # 1. إدراج المقرر في جدول courses
            cursor.execute(
                'INSERT INTO courses (name, room_type, teacher_id) VALUES (?, ?, ?)',
                (course.get('name'), course.get('room_type'), teacher_map.get(course.get('teacher_name')))
            )
            course_id = cursor.lastrowid

            # 2. ✨ منطق ذكي للتعامل مع النسق القديم والجديد
            levels_for_course = []
            if 'levels' in course and isinstance(course['levels'], list):
                # النسق الجديد: 'levels' هي قائمة
                levels_for_course = course['levels']
            elif 'level' in course:
                # النسق القديم: 'level' هي نص واحد
                levels_for_course = [course['level']]
            
            # 3. ربط المقرر بالمستويات في جدول course_levels
            for level_name in levels_for_course:
                if level_name in level_map:
                    level_id = level_map[level_name]
                    cursor.execute('INSERT OR IGNORE INTO course_levels (course_id, level_id) VALUES (?, ?)', (course_id, level_id))

        # استعادة الإعدادات
        if 'settings' in backup_data:
            settings_json = json.dumps(backup_data.get('settings', {}), ensure_ascii=False)
            cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', ('main_settings', settings_json))

        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "message": "تم استعادة البيانات بنجاح. سيتم إعادة تحميل الصفحة."})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"حدث خطأ أثناء استعادة البيانات: {str(e)}"}), 500

@app.route('/api/reset-all', methods=['POST'])
def reset_all_data():
    """
    يقوم بمسح محتوى كل جداول البيانات.
    """
    try:
        execute_db('DELETE FROM courses')
        execute_db('DELETE FROM rooms')
        execute_db('DELETE FROM teachers')
        execute_db('DELETE FROM levels')
        execute_db('DELETE FROM settings')
        # إعادة إعدادات فارغة
        execute_db('INSERT INTO settings (key, value) VALUES (?, ?)', ('main_settings', '{}'))
        
        return jsonify({"success": True, "message": "تم مسح جميع البيانات بنجاح. سيتم إعادة تحميل الصفحة."})

    except Exception as e:
        return jsonify({"error": f"حدث خطأ أثناء مسح البيانات: {str(e)}"}), 500
    
@app.route('/api/data-template', methods=['GET'])
def export_data_template():
    """
    ينشئ ويصدر ملف Excel يحتوي على جميع البيانات الحالية.
    إذا كانت قاعدة البيانات فارغة، سيتم تصدير قالب بالرؤوس فقط.
    --- نسخة محدثة مع أعمدة أوسع ---
    """
    try:
        # 1. استعلام لجلب كل البيانات الموجودة من قاعدة البيانات
        teachers_data = query_db('SELECT name FROM teachers')
        rooms_data = query_db('SELECT name, type FROM rooms')
        levels_data = query_db('SELECT name FROM levels')
        
        # استعلام معقد لجلب المقررات مع قائمة مستوياتها مجمعة
        courses_query = '''
            SELECT 
                c.name, 
                GROUP_CONCAT(l.name, '، ') as levels,
                c.room_type
            FROM courses c
            LEFT JOIN course_levels cl ON c.id = cl.course_id
            LEFT JOIN levels l ON cl.level_id = l.id
            GROUP BY c.id, c.name, c.room_type
            ORDER BY c.name
        '''
        courses_data = query_db(courses_query)

        # 2. إنشاء DataFrames باستخدام مكتبة Pandas من البيانات التي تم جلبها
        df_teachers = pd.DataFrame(teachers_data, columns=['name'])
        df_rooms = pd.DataFrame(rooms_data, columns=['name', 'type'])
        df_levels = pd.DataFrame(levels_data, columns=['name'])
        df_courses = pd.DataFrame(courses_data, columns=['name', 'levels', 'room_type'])

        # 3. إعادة تسمية أعمدة الـ DataFrames لتطابق أسماء الأعمدة في القالب
        df_teachers.rename(columns={'name': 'اسم الأستاذ'}, inplace=True)
        df_rooms.rename(columns={'name': 'اسم القاعة', 'type': 'نوع القاعة (كبيرة/صغيرة)'}, inplace=True)
        df_levels.rename(columns={'name': 'اسم المستوى الدراسي'}, inplace=True)
        df_courses.rename(columns={
            'name': 'اسم المقرر', 
            'levels': 'المستوى الدراسي (افصل بين المستويات بفاصلة ،)', 
            'room_type': 'نوع القاعة المطلوب (كبيرة/صغيرة)'
        }, inplace=True)

        # 4. كتابة الـ DataFrames إلى ملف Excel
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_teachers.to_excel(writer, sheet_name='الأساتذة', index=False)
            df_rooms.to_excel(writer, sheet_name='القاعات', index=False)
            df_levels.to_excel(writer, sheet_name='المستويات', index=False)
            df_courses.to_excel(writer, sheet_name='المقررات', index=False)
            
            workbook = writer.book
            for sheet_name in workbook.sheetnames:
                worksheet = workbook[sheet_name]
                # تفعيل واجهة من اليمين لليسار
                worksheet.sheet_view.rightToLeft = True
                
                # --- ✅ الإضافة الجديدة: تعديل عرض الأعمدة ---
                # يتم تحديد العرض بوحدة قريبة من عدد الأحرف
                worksheet.column_dimensions['A'].width = 40  # عرض العمود الأول (اسم المادة/الأستاذ/...)
                worksheet.column_dimensions['B'].width = 50  # عرض العمود الثاني (المستويات/نوع القاعة)
                worksheet.column_dimensions['C'].width = 25  # عرض العمود الثالث (نوع القاعة للمقررات)

        output.seek(0)
        return send_file(output, as_attachment=True, download_name='بيانات_الجدول_الحالية.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        traceback.print_exc() 
        return jsonify({"error": f"فشل إنشاء الملف: {e}"}), 500

@app.route('/api/import-data', methods=['POST'])
def import_data_from_file():
    """
    يستورد البيانات من ملف Excel ويضيفها إلى قاعدة البيانات.
    """
    if 'file' not in request.files:
        return jsonify({"error": "لم يتم العثور على ملف."}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "لم يتم تحديد أي ملف."}), 400

    try:
        # نقرأ كل الأعمدة كنص لمنع تحويل الأرقام تلقائياً
        xls = pd.read_excel(file, sheet_name=None, dtype=str)
        conn = get_db_connection()
        cursor = conn.cursor()

        if 'الأساتذة' in xls:
            df = xls['الأساتذة'].dropna(how='all')
            for name in df.iloc[:, 0]: # نقرأ من العمود الأول دائماً
                cursor.execute("INSERT OR IGNORE INTO teachers (name) VALUES (?)", (str(name).strip(),))

        if 'القاعات' in xls:
            df = xls['القاعات'].dropna(how='all')
            for _, row in df.iterrows():
                cursor.execute("INSERT OR IGNORE INTO rooms (name, type) VALUES (?, ?)", (str(row.iloc[0]).strip(), str(row.iloc[1]).strip()))

        if 'المستويات' in xls:
            df = xls['المستويات'].dropna(how='all')
            for name in df.iloc[:, 0]:
                cursor.execute("INSERT OR IGNORE INTO levels (name) VALUES (?)", (str(name).strip(),))

        conn.commit()

        if 'المقررات' in xls:
            level_map = {row['name']: row['id'] for row in query_db("SELECT id, name FROM levels")}
            df = xls['المقررات'].dropna(how='all')
            for _, row in df.iterrows():
                name = str(row.iloc[0]).strip()
                # ✨ قراءة المستويات كنص واحد ثم تقسيمه
                levels_str = str(row.iloc[1]).strip()
                room_type = str(row.iloc[2]).strip()
                
                if not all([name, levels_str, room_type]): continue
                
                # ✨ إدراج المقرر في جدول courses أولاً
                cursor.execute("INSERT INTO courses (name, room_type) VALUES (?, ?)", (name, room_type))
                course_id = cursor.lastrowid
                
                # ✨ تقسيم المستويات وربط كل واحد منها بالمقرر
                level_names = [lvl.strip() for lvl in levels_str.split('،')]
                for level_name in level_names:
                    if level_name in level_map:
                        level_id = level_map[level_name]
                        cursor.execute("INSERT OR IGNORE INTO course_levels (course_id, level_id) VALUES (?, ?)", (course_id, level_id))

        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم استيراد البيانات بنجاح. سيتم إعادة تحميل الصفحة."})

    except Exception as e:
        return jsonify({"error": f"فشل تحليل الملف. تأكد من أن أسماء الأعمدة متطابقة مع القالب. الخطأ: {e}"}), 500

@app.route('/api/dashboard-stats', methods=['GET'])
def get_dashboard_stats():
    """
    تجلب الإحصائيات العامة (عدد الأساتذة، القاعات، إلخ) من قاعدة البيانات.
    """
    try:
        conn = get_db_connection()
        teachers_count = conn.execute('SELECT COUNT(*) FROM teachers').fetchone()[0]
        rooms_count = conn.execute('SELECT COUNT(*) FROM rooms').fetchone()[0]
        levels_count = conn.execute('SELECT COUNT(*) FROM levels').fetchone()[0]
        courses_count = conn.execute('SELECT COUNT(*) FROM courses').fetchone()[0]
        conn.close()
        return jsonify({
            "teachers_count": teachers_count,
            "rooms_count": rooms_count,
            "levels_count": levels_count,
            "courses_count": courses_count
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
# =====================================================================
# START: LARGE NEIGHBORHOOD SEARCH (LNS)
# =====================================================================
def run_large_neighborhood_search(log_q, all_lectures, days, slots, rooms_data, teachers, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, max_iterations, ruin_factor, prioritize_primary, scheduling_state, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=None, consecutive_large_hall_rule="none", progress_channel=None):
    
    
    log_q.put('--- بدء خوارزمية البحث الجِوَاري الواسع (LNS) ---')
    
    # --- الخطوة 1: إنشاء حل أولي ---
    
    log_q.put('   - جاري إنشاء حل أولي باستخدام الخوارزمية الطماعة...')
    
    # تقسيم الحصص إلى أساسية واحتياطية (للقاعات الكبرى)
    primary_slots, reserve_slots = [], []
    day_indices_shuffled = list(range(len(days)))
    random.shuffle(day_indices_shuffled)
    for day_idx in day_indices_shuffled:
        for slot_idx in range(len(slots)):
            is_primary = any(rule.get('rule_type') == 'SPECIFIC_LARGE_HALL' for rule in rules_grid[day_idx][slot_idx])
            (primary_slots if is_primary else reserve_slots).append((day_idx, slot_idx))

    # ترتيب المحاضرات حسب الصعوبة
    lectures_sorted_for_greedy = sorted(
        all_lectures,
        key=lambda lec: calculate_lecture_difficulty(lec, lectures_by_teacher_map.get(lec.get('teacher_name'), []), special_constraints, teacher_constraints),
        reverse=True
    )
    
    initial_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
    teacher_schedule_greedy = {t['name']: set() for t in teachers}
    room_schedule_greedy = {r['name']: set() for r in rooms_data}
    
    # ✨ تتبع المواد التي لم توضع في الحل الأولي
    unplaced_lectures = []
    for lecture in lectures_sorted_for_greedy:
        success, message = find_slot_for_single_lecture(
            lecture, initial_schedule, teacher_schedule_greedy, room_schedule_greedy,
            days, slots, rules_grid, rooms_data, teacher_constraints, globally_unavailable_slots, 
            special_constraints, primary_slots, reserve_slots, identifiers_by_level,
            prioritize_primary, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule
        )
        if not success:
            unplaced_lectures.append({"course_name": lecture.get('name'), "teacher_name": lecture.get('teacher_name'), "reason": message})

    current_solution = initial_schedule
    best_solution_so_far = copy.deepcopy(current_solution)
    
    cost_list = calculate_schedule_cost(current_solution, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)
    
    # ✨ تعديل حساب التكلفة الأولية (إعطاء عقوبة ضخمة للمواد غير الموزعة)
    current_cost = len(cost_list) + (len(unplaced_lectures) * 1000)
    best_cost_so_far = current_cost
    
    
    log_q.put(f'   - التكلفة الأولية (عدد الأخطاء) = {len(cost_list)}, المواد غير الموزعة = {len(unplaced_lectures)}')
    time.sleep(0)

    last_progress_report = 0
    progress_report_interval = max(50, max_iterations // 20)
    
    # --- الخطوة 2: حلقة LNS الرئيسية (مع تعديل حساب التكلفة) ---
    for i in range(max_iterations):
        if i % 10 == 0:
            if scheduling_state.get('should_stop'): 
                log_q.put(f'\n--- تم إيقاف LNS عند التكرار {i+1} ---')
                
                raise StopByUserException()
        
        if best_cost_so_far < 1000: # نتوقف إذا تم توزيع كل المواد وحققت نتيجة جيدة
            if best_cost_so_far == 0:
                log_q.put('   - تم العثور على حل مثالي! إنهاء البحث.')
                break

        if i - last_progress_report >= progress_report_interval:
            log_q.put(f'--- الدورة {i + 1}/{max_iterations} | أفضل تكلفة = {best_cost_so_far} ---')
            time.sleep(0.05)
            last_progress_report = i

        new_solution_candidate = copy.deepcopy(current_solution)
        
        # --- اختيار الأساتذة للتخريب (Ruin) ---
        unique_teacher_names = list({t['name'] for t in teachers})
        if not unique_teacher_names: continue
            
        adaptive_ruin_factor = ruin_factor * (1 - (i / max_iterations) * 0.5)
        num_to_ruin = max(1, min(int(len(unique_teacher_names) * adaptive_ruin_factor), len(unique_teacher_names)))
        
        # اختيار الأساتذة بناءً على التعارضات
        current_failures_list = calculate_schedule_cost(current_solution, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)
        prof_conflict_counts = defaultdict(int)
        for failure in current_failures_list:
            teacher = failure.get('teacher_name')
            if teacher and teacher in unique_teacher_names:
                prof_conflict_counts[teacher] += 1
        
        base_weight, conflict_weight = 1, 10
        teacher_weights = [base_weight + (prof_conflict_counts.get(name, 0) * conflict_weight) for name in unique_teacher_names]

        if sum(prof_conflict_counts.values()) > 0:
            professors_to_ruin = list(set(random.choices(unique_teacher_names, weights=teacher_weights, k=num_to_ruin)))
        else:
            professors_to_ruin = random.sample(unique_teacher_names, num_to_ruin)
        
        # --- عملية التخريب وإعادة البناء (Ruin & Repair) ---
        lectures_to_reinsert = [lec for lec in all_lectures if lec.get('teacher_name') in professors_to_ruin]
        ids_to_remove = {lec.get('id') for lec in lectures_to_reinsert}
        
        for level_grid in new_solution_candidate.values():
            for day_slots in level_grid:
                for slot_lectures in day_slots:
                    slot_lectures[:] = [lec for lec in slot_lectures if lec.get('id') not in ids_to_remove]
        
        teacher_schedule_rebuild = {t['name']: set() for t in teachers}
        room_schedule_rebuild = {r['name']: set() for r in rooms_data}
        for grid in new_solution_candidate.values():
            for day_idx, day in enumerate(grid):
                for slot_idx, lectures in enumerate(day):
                    for lec in lectures:
                        teacher_schedule_rebuild.setdefault(lec['teacher_name'], set()).add((day_idx, slot_idx))
                        if lec.get('room'): 
                            room_schedule_rebuild.setdefault(lec['room'], set()).add((day_idx, slot_idx))

        # ✨ تتبع المواد غير الموزعة في هذه الدورة
        unplaced_in_iteration = []
        lectures_to_reinsert_sorted = sorted(
            lectures_to_reinsert, 
            key=lambda lec: calculate_lecture_difficulty(lec, lectures_by_teacher_map.get(lec.get('teacher_name'), []), special_constraints, teacher_constraints), 
            reverse=True
        )

        for lecture in lectures_to_reinsert_sorted:
            success, message = find_slot_for_single_lecture(
                lecture, new_solution_candidate, teacher_schedule_rebuild, room_schedule_rebuild, 
                days, slots, rules_grid, rooms_data, teacher_constraints, globally_unavailable_slots, 
                special_constraints, primary_slots, reserve_slots, identifiers_by_level, 
                prioritize_primary, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule
            )
            if not success:
                unplaced_in_iteration.append({"course_name": lecture.get('name'), "teacher_name": lecture.get('teacher_name'), "reason": message})
        
        new_cost_list = calculate_schedule_cost(new_solution_candidate, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)
        
        # ✨ --- تعديل جوهري: إعطاء وزن ضخم للمواد غير الموزعة ---
        new_cost = len(new_cost_list) + (len(unplaced_in_iteration) * 1000)

        # --- معيار القبول (Simulated Annealing) ---
        temperature = 1.0 - (i / max_iterations)
        if new_cost < current_cost or (temperature > 0.1 and random.random() < math.exp(-(new_cost - current_cost) / temperature)):
            current_solution = new_solution_candidate
            current_cost = new_cost
            
            if current_cost < best_cost_so_far:
                best_cost_so_far = current_cost
                best_solution_so_far = copy.deepcopy(current_solution)
                if progress_channel: progress_channel['best_solution_so_far'] = best_solution_so_far
                
                # إرسال تحديثات عند إيجاد حل أفضل
                num_violations = best_cost_so_far % 1000
                num_unplaced = best_cost_so_far // 1000
                
                log_q.put(f'   >>> إنجاز جديد في التكرار {i+1}! فشل قيود = {num_violations}, مواد ناقصة = {num_unplaced}')
                
                # تحديث شريط التقدم بناءً على جودة الحل
                progress_percentage = max(0, (10 - num_violations) / 10 * 100) if num_unplaced == 0 else 5.0
                log_q.put(f"PROGRESS:{progress_percentage:.1f}")

    # --- الخطوة 3: التحقق النهائي وإرجاع النتيجة ---
    log_q.put(f'انتهت خوارزمية LNS بعد {min(i+1, max_iterations)} تكرار.')
    
    
    # فحص التعارضات في أفضل حل تم العثور عليه
    final_constraint_violations = calculate_schedule_cost(best_solution_so_far, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)

    # ✨ فحص نهائي للتأكد من عدم وجود نقص في المواد
    final_unplaced_lectures = []
    scheduled_ids = {lec.get('id') for grid in best_solution_so_far.values() for day in grid for slot in day for lec in slot}
    for lec in all_lectures:
        if lec.get('id') not in scheduled_ids:
            final_unplaced_lectures.append({
                "course_name": lec.get('name'),
                "teacher_name": lec.get('teacher_name'),
                "reason": "المادة لم يتم جدولتها في الحل النهائي (نقص)."
            })
            
    final_failures_list = final_constraint_violations + final_unplaced_lectures
    final_cost = len(final_failures_list)
    
    final_progress = max(0, (10 - final_cost) / 10 * 100) if not final_unplaced_lectures else 0
    log_q.put(f"PROGRESS:{final_progress:.1f}")
    
    log_q.put(f'=== انتهى LNS نهائياً - أفضل تكلفة: {final_cost} ===')
    time.sleep(0.1)
    
    return best_solution_so_far, final_cost, final_failures_list
# =====================================================================
# END: LARGE NEIGHBORHOOD SEARCH (LNS)
# =====================================================================

# =====================================================================
# START: VARIABLE NEIGHBORHOOD SEARCH (VNS) - الكود الجديد
# =====================================================================
def run_variable_neighborhood_search(
    log_q, all_lectures, days, slots, rooms_data, teachers, all_levels,
    identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type,
    lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs,
    day_to_idx, rules_grid, max_iterations, k_max, prioritize_primary,
    scheduling_state, last_slot_restrictions, level_specific_large_rooms,
    specific_small_room_assignments, max_sessions_per_day=None, consecutive_large_hall_rule="none", progress_channel=None):

    # --- المرحلة 1: الإعداد والبناء المبدئي (من الكود الأصلي) ---
    
    log_q.put('--- بدء VNS (مع منطق الإصلاح أولاً) ---')
    
    primary_slots, reserve_slots = [], []
    day_indices_shuffled = list(range(len(days)))
    random.shuffle(day_indices_shuffled)
    for day_idx in day_indices_shuffled:
        for slot_idx in range(len(slots)):
            is_primary = any(rule.get('rule_type') == 'SPECIFIC_LARGE_HALL' for rule in rules_grid[day_idx][slot_idx])
            (primary_slots if is_primary else reserve_slots).append((day_idx, slot_idx))

    lectures_sorted_for_greedy = sorted(
        all_lectures,
        key=lambda lec: calculate_lecture_difficulty(lec, lectures_by_teacher_map.get(lec.get('teacher_name'), []), special_constraints, teacher_constraints),
        reverse=True
    )

    initial_schedule = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
    teacher_schedule_greedy = {t['name']: set() for t in teachers}
    room_schedule_greedy = {r['name']: set() for r in rooms_data}
    initial_failures = []
    
    for lecture in lectures_sorted_for_greedy:
        success, message = find_slot_for_single_lecture(
            lecture, initial_schedule, teacher_schedule_greedy, room_schedule_greedy,
            days, slots, rules_grid, rooms_data, teacher_constraints, globally_unavailable_slots, 
            special_constraints, primary_slots, reserve_slots, identifiers_by_level,
            prioritize_primary, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule
        )
        if not success:
            initial_failures.append({"course_name": lecture.get('name'), "teacher_name": lecture.get('teacher_name'), "reason": message})
    
    # --- المرحلة 2: حساب التكلفة الأولية (من الكود الأصلي مع تعديل طفيف من القالب) ---
    current_solution = initial_schedule
    cost_list = calculate_schedule_cost(current_solution, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)
    
    # استخدام `initial_failures` التي تم حسابها للتو
    current_cost = len(cost_list) + (len(initial_failures) * 1000)
    best_solution_so_far = copy.deepcopy(current_solution)
    best_cost_so_far = current_cost
    
    log_q.put(f'   - اكتمل البناء المبدئي (تعارضات={len(cost_list)}, نقص={len(initial_failures)})')

    # --- بداية الإضافة: تعريف عدّاد الركود ---
    unplaced_stagnation_counter = 0
    last_unplaced_count = float('inf')
    STAGNATION_LIMIT = 5 # يمكنك تعديل هذا الرقم
    unplaced_lectures_final = list(initial_failures) # احتفظ بقائمة الفشل
    # --- نهاية الإضافة ---

    # --- المرحلة 3: حلقة VNS الرئيسية للتحسين (مع التعديل الجوهري) ---
    for i in range(max_iterations):
        if scheduling_state.get('should_stop'): raise StopByUserException()
        if best_cost_so_far == 0: break
        
        
        log_q.put(f'--- دورة التحسين {i + 1}/{max_iterations} | أفضل تكلفة = {best_cost_so_far} ---')
        time.sleep(0.01)

        # ✨ --- بداية التعديل الجوهري: تحديد النقص قبل كل شيء ---
        current_scheduled_ids = {lec.get('id') for grid in current_solution.values() for day in grid for slot in day for lec in slot}
        currently_unplaced = [lec for lec in all_lectures if lec.get('id') not in current_scheduled_ids and lec.get('teacher_name')]
        
        # --- بداية الإضافة: منطق كشف الركود ---
        if len(currently_unplaced) > 0 and len(currently_unplaced) == last_unplaced_count:
            unplaced_stagnation_counter += 1
        else:
            unplaced_stagnation_counter = 0

        last_unplaced_count = len(currently_unplaced)

        if unplaced_stagnation_counter > STAGNATION_LIMIT:
            log_q.put(f"!!! توقف تلقائي: فشلت الخوارزمية في إدراج المواد الناقصة لـ {STAGNATION_LIMIT} محاولة متتالية.")
            unplaced_lectures_final.extend([
                {"course_name": lec.get('name'), "teacher_name": lec.get('teacher_name'), "reason": "فشل إدراجه بشكل متكرر (توقف تلقائي)."}
                for lec in currently_unplaced if not any(f.get('course_name') == lec.get('name') for f in unplaced_lectures_final)
            ])
            break # الخروج من حلقة البحث الرئيسية
        # --- نهاية الإضافة ---

        k = 1
        while k <= k_max:
            shaken_solution = copy.deepcopy(current_solution)
            unplaced_in_iteration = []

            temp_teacher_schedule_shake = defaultdict(set)
            temp_room_schedule_shake = defaultdict(set)
            for grid in shaken_solution.values():
                for d_idx, day in enumerate(grid):
                    for s_idx, lectures in enumerate(day):
                        for lec in lectures:
                            if lec.get('teacher_name'): temp_teacher_schedule_shake[lec['teacher_name']].add((d_idx, s_idx))
                            if lec.get('room'): temp_room_schedule_shake[lec.get('room')].add((d_idx, s_idx))

            # ✨ --- الأولوية القصوى: محاولة إدراج المواد الناقصة ---
            if currently_unplaced:
                log_q.put(f'   * أولوية قصوى: محاولة إدراج مادة ناقصة ({len(currently_unplaced)} متبقية)...')
                
                lecture_to_insert = random.choice(currently_unplaced)
                
                success, message = find_slot_for_single_lecture(lecture_to_insert, shaken_solution, temp_teacher_schedule_shake, temp_room_schedule_shake, days, slots, rules_grid, rooms_data, teacher_constraints, globally_unavailable_slots, special_constraints, primary_slots, reserve_slots, identifiers_by_level, prioritize_primary, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule)
                if not success:
                    unplaced_in_iteration.append(lecture_to_insert)
                
                for lec in currently_unplaced:
                    if lec.get('id') != lecture_to_insert.get('id'):
                        unplaced_in_iteration.append(lec)

            # --- إذا كان الجدول كاملاً، قم بالهز العادي ---
            else:
                num_to_shake = min(k, len(all_lectures))
                if not all_lectures: break
                lectures_to_reinsert = random.sample(all_lectures, num_to_shake)
                
                ids_to_remove = {l.get('id') for l in lectures_to_reinsert}
                for grid in shaken_solution.values():
                    for day in grid:
                        for slot in day:
                            slot[:] = [l for l in slot if l.get('id') not in ids_to_remove]
                
                # إعادة بناء جداول المساعدة بعد الحذف (من الكود الأصلي)
                temp_teacher_schedule_shake.clear(); temp_room_schedule_shake.clear()
                for grid in shaken_solution.values():
                    for d_idx, day in enumerate(grid):
                        for s_idx, lectures_in_slot in enumerate(day):
                            for lec in lectures_in_slot:
                                temp_teacher_schedule_shake[lec['teacher_name']].add((d_idx, s_idx))
                                if lec.get('room'): temp_room_schedule_shake[lec.get('room')].add((d_idx, s_idx))

                lectures_to_reinsert_sorted = sorted(
                    lectures_to_reinsert,
                    key=lambda lec: calculate_lecture_difficulty(lec, lectures_by_teacher_map.get(lec.get('teacher_name'), []), special_constraints, teacher_constraints),
                    reverse=True
                )

                for lecture in lectures_to_reinsert_sorted:
                    success, message = find_slot_for_single_lecture(lecture, shaken_solution, temp_teacher_schedule_shake, temp_room_schedule_shake, days, slots, rules_grid, rooms_data, teacher_constraints, globally_unavailable_slots, special_constraints, primary_slots, reserve_slots, identifiers_by_level, prioritize_primary, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule)
                    if not success:
                        unplaced_in_iteration.append({"course_name": lecture.get('name'), "teacher_name": lecture.get('teacher_name'), "reason": message})

            # --- حساب التكلفة والقبول (من الكود الأصلي) ---
            new_cost_list = calculate_schedule_cost(shaken_solution, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)
            new_cost = len(new_cost_list) + (len(unplaced_in_iteration) * 1000)

            if new_cost < current_cost:
                current_solution = shaken_solution; current_cost = new_cost
                log_q.put(f'   * تحسين عند الجوار k={k}. التكلفة الجديدة = {current_cost}')
                
                k = 1
                if new_cost < best_cost_so_far:
                    best_cost_so_far = new_cost
                    best_solution_so_far = copy.deepcopy(current_solution)
                    if progress_channel: progress_channel['best_solution_so_far'] = best_solution_so_far
                    
                    num_violations = best_cost_so_far % 1000
                    num_unplaced = best_cost_so_far // 1000
                    log_q.put(f'   >>> إنجاز جديد! تعارضات={num_violations}, نقص={num_unplaced}')
                    
                    progress_percentage = max(0, (10 - num_violations) / 10 * 100) if num_unplaced == 0 else 5.0
                    log_q.put(f"PROGRESS:{progress_percentage:.1f}")
            else:
                k += 1
    
    # --- الفحص النهائي وإرجاع النتيجة (من الكود الأصلي) ---
    
    log_q.put('انتهت خوارزمية VNS.')
    final_constraint_violations = calculate_schedule_cost(best_solution_so_far, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)

    # --- بداية الإضافة: استخدام قائمة الفشل المحدثة ---
    # إذا توقفت الخوارزمية بسبب الركود، ستكون هذه القائمة تحتوي على سبب التوقف
    final_unplaced_lectures = unplaced_lectures_final
    # --- نهاية الإضافة ---

    final_failures_list = final_constraint_violations + final_unplaced_lectures
    final_cost = len(final_failures_list)
            
    return best_solution_so_far, final_cost, final_failures_list
# =====================================================================
# END: VARIABLE NEIGHBORHOOD SEARCH (VNS)
# =====================================================================

# =====================================================================
# START: FLEXIBLE VNS ALGORITHM
# =====================================================================

# استبدل الدالة القديمة بالكامل بهذه النسخة المصححة
def run_vns_with_flex_assignments(
    log_q, all_lectures, days, slots, rooms_data, teachers, all_levels,
    identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type,
    lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs,
    day_to_idx, rules_grid, max_iterations, k_max, prioritize_primary,
    scheduling_state, last_slot_restrictions, level_specific_large_rooms,
    specific_small_room_assignments, flexible_categories, max_sessions_per_day=None,
    # المعاملات الجديدة للبدء من حل مبدئي
    initial_schedule=None, initial_teacher_schedule=None, initial_room_schedule=None, consecutive_large_hall_rule="none", progress_channel=None
):
    
    # --- المرحلة 1: الإعداد وإسناد المواد المرنة ---
    
    log_q.put('--- بدء VNS المرن (مع الإسناد التلقائي) ---')
    
    all_flexible_course_names = set()
    flex_pools = {}
    if flexible_categories:
        for category in flexible_categories:
            course_names = category.get('courses', [])
            all_flexible_course_names.update(course_names)
            cat_id = category.get('id', f"cat_{len(flex_pools)}")
            lectures_in_pool = [lec for lec in all_lectures if lec.get('name') in course_names]
            flex_pools[cat_id] = { "professors": category.get('professors', []), "lectures": lectures_in_pool }

    flexible_unassigned_lectures = []
    for lecture in all_lectures:
        if lecture.get('name') in all_flexible_course_names and not lecture.get('teacher_name'):
            flexible_unassigned_lectures.append(lecture)

    unassigned_due_to_quota = []
    if flexible_unassigned_lectures:
        log_q.put(' - الخطوة 1: إسناد الأساتذة للمواد المرنة...')
        
        prof_quotas = defaultdict(int)
        course_to_category_map = {}
        for category in flexible_categories:
            for prof in category.get('professors', []): prof_quotas[prof['name']] += prof.get('quota', 1)
            for course_name in category.get('courses', []): course_to_category_map[course_name] = category
        
        for lecture in flexible_unassigned_lectures:
            category = course_to_category_map.get(lecture.get('name'))
            if not category:
                unassigned_due_to_quota.append({"course_name": lecture.get('name'), "reason": "لم يتم العثور على فئة مرنة لها."}); continue
            available_profs = [p['name'] for p in category.get('professors', []) if prof_quotas[p['name']] > 0]
            if not available_profs:
                unassigned_due_to_quota.append({"course_name": lecture.get('name'), "reason": "لا يوجد أساتذة بحصة متاحة."}); continue
            chosen_prof = random.choice(available_profs)
            lecture['teacher_name'] = chosen_prof
            prof_quotas[chosen_prof] -= 1
    
    log_q.put(' - بناء خريطة الإسناد المبدئية...')
    updated_lectures_by_teacher_map = defaultdict(list)
    for lec in all_lectures:
        if lec.get('teacher_name'):
            updated_lectures_by_teacher_map[lec.get('teacher_name')].append(lec)

    # --- المرحلة 2: بناء الحل المبدئي (الجزء المصحح والمدمج) ---
    
    primary_slots, reserve_slots = [], []
    for day_idx in range(len(days)):
        for slot_idx in range(len(slots)):
            (primary_slots if any(r.get('rule_type') == 'SPECIFIC_LARGE_HALL' for r in rules_grid[day_idx][slot_idx]) else reserve_slots).append((day_idx, slot_idx))

    if initial_schedule is not None:
        log_q.put(' - الخطوة 2: الانطلاق من الجدول المجهز مسبقًا...')
        current_solution = copy.deepcopy(initial_schedule)

        if initial_teacher_schedule is None:
            log_q.put('   - تحذير: initial_teacher_schedule لم يتم توفيره. سيتم بناؤه من الجدول الأولي.')
            initial_teacher_schedule = defaultdict(set)
            for grid in initial_schedule.values():
                for d, day in enumerate(grid):
                    for s, slot in enumerate(day):
                        for lec in slot:
                            if lec.get('teacher_name'):
                                initial_teacher_schedule[lec['teacher_name']].add((d, s))
        
        if initial_room_schedule is None:
            log_q.put('   - تحذير: initial_room_schedule لم يتم توفيره. سيتم بناؤه من الجدول الأولي.')
            initial_room_schedule = defaultdict(set)
            for grid in initial_schedule.values():
                for d, day in enumerate(grid):
                    for s, slot in enumerate(day):
                        for lec in slot:
                            if lec.get('room'):
                                initial_room_schedule[lec['room']].add((d, s))

        temp_teacher_schedule = copy.deepcopy(initial_teacher_schedule)
        temp_room_schedule = copy.deepcopy(initial_room_schedule)
        unplaced_lectures = unassigned_due_to_quota
        
        all_scheduled_ids = {lec['id'] for grid in current_solution.values() for day in grid for slot in day for lec in slot}
        lectures_to_place = [
            lec for lec in all_lectures 
            if lec.get('teacher_name') and lec.get('id') not in all_scheduled_ids
        ]
        
        for lecture in sorted(lectures_to_place, key=lambda l: calculate_lecture_difficulty(l, updated_lectures_by_teacher_map.get(l.get('teacher_name'), []), special_constraints, teacher_constraints), reverse=True):
            success, message = find_slot_for_single_lecture(lecture, current_solution, temp_teacher_schedule, temp_room_schedule, days, slots, rules_grid, rooms_data, teacher_constraints, globally_unavailable_slots, special_constraints, primary_slots, reserve_slots, identifiers_by_level, prioritize_primary, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule)
            if not success:
                unplaced_lectures.append({"course_name": lecture.get('name'), "teacher_name": lecture.get('teacher_name'), "reason": message})
    else:
        log_q.put(' - الخطوة 2: جدولة جميع المواد من الصفر...')
        current_solution = {level: [[[] for _ in slots] for _ in days] for level in all_levels}
        temp_teacher_schedule = defaultdict(set)
        temp_room_schedule = defaultdict(set)
        unplaced_lectures = unassigned_due_to_quota

        for lecture in sorted(all_lectures, key=lambda l: calculate_lecture_difficulty(l, updated_lectures_by_teacher_map.get(l.get('teacher_name'), []), special_constraints, teacher_constraints), reverse=True):
            if not lecture.get('teacher_name'): continue
            success, message = find_slot_for_single_lecture(lecture, current_solution, temp_teacher_schedule, temp_room_schedule, days, slots, rules_grid, rooms_data, teacher_constraints, globally_unavailable_slots, special_constraints, primary_slots, reserve_slots, identifiers_by_level, prioritize_primary, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule)
            if not success:
                unplaced_lectures.append({"course_name": lecture.get('name'), "teacher_name": lecture.get('teacher_name'), "reason": message})

    # --- المرحلة 3: حلقة VNS الرئيسية للتحسين (كاملة) ---
    
    cost_list = calculate_schedule_cost(current_solution, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, updated_lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)
    current_cost = len(cost_list) + (len(unplaced_lectures) * 1000)
    best_solution_so_far = copy.deepcopy(current_solution)
    best_cost_so_far = current_cost
    
    log_q.put(f' - اكتمل البناء المبدئي (تعارضات={len(cost_list)}, نقص={len(unplaced_lectures)})')
    
    non_flexible_lecture_ids = {lec.get('id') for lec in all_lectures if lec.get('name') not in all_flexible_course_names}

    unplaced_stagnation_counter = 0
    last_unplaced_count = float('inf')
    STAGNATION_LIMIT = 5  # إيقاف البحث بعد 30 محاولة فاشلة متتالية لإدراج المواد
    
    for i in range(max_iterations):
        if scheduling_state.get('should_stop'): raise StopByUserException()
        if best_cost_so_far == 0: break
        
        log_q.put(f'--- دورة التحسين {i + 1}/{max_iterations} | أفضل تكلفة = {best_cost_so_far} ---')
        time.sleep(0.01)

        current_scheduled_ids = {lec.get('id') for grid in current_solution.values() for day in grid for slot in day for lec in slot}
        currently_unplaced = [lec for lec in all_lectures if lec.get('id') not in current_scheduled_ids and lec.get('teacher_name')]
        
        # --- بداية الإضافة: منطق كشف الركود ---
        if len(currently_unplaced) > 0 and len(currently_unplaced) == last_unplaced_count:
            unplaced_stagnation_counter += 1
        else:
            unplaced_stagnation_counter = 0

        last_unplaced_count = len(currently_unplaced)

        if unplaced_stagnation_counter > STAGNATION_LIMIT:
            log_q.put(f"!!! توقف تلقائي: فشلت الخوارزمية في إدراج المواد الناقصة لـ {STAGNATION_LIMIT} محاولة متتالية.")
            log_q.put("... قد تكون القيود المفروضة شديدة الصرامة أو متضاربة.")
            # إضافة المواد التي لم توضع إلى قائمة الفشل النهائية
            unplaced_lectures.extend([
                {"course_name": lec.get('name'), "teacher_name": lec.get('teacher_name'), "reason": "فشل إدراجه بشكل متكرر (توقف تلقائي)."}
                for lec in currently_unplaced
            ])
            break # الخروج من حلقة البحث الرئيسية
        # --- نهاية الإضافة ---

        k = 1
        while k <= k_max:
            shaken_solution = copy.deepcopy(current_solution)
            unplaced_in_iteration = []
            swap_move_made = False
            lec1, lec2, t1_name, t2_name = None, None, None, None
            
            temp_teacher_schedule_shake = defaultdict(set)
            temp_room_schedule_shake = defaultdict(set)
            for grid in shaken_solution.values():
                for d_idx, day in enumerate(grid):
                    for s_idx, lectures in enumerate(day):
                        for lec in lectures:
                            if lec.get('teacher_name'): temp_teacher_schedule_shake[lec['teacher_name']].add((d_idx, s_idx))
                            if lec.get('room'): temp_room_schedule_shake[lec.get('room')].add((d_idx, s_idx))

            if currently_unplaced:
                log_q.put(f'   * أولوية قصوى: محاولة إدراج مادة ناقصة ({len(currently_unplaced)} متبقية)...')
                lecture_to_insert = random.choice(currently_unplaced)
                
                success, message = find_slot_for_single_lecture(lecture_to_insert, shaken_solution, temp_teacher_schedule_shake, temp_room_schedule_shake, days, slots, rules_grid, rooms_data, teacher_constraints, globally_unavailable_slots, special_constraints, primary_slots, reserve_slots, identifiers_by_level, prioritize_primary, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule)
                if not success:
                    unplaced_in_iteration.append(lecture_to_insert)

                for lec in currently_unplaced:
                    if lec.get('id') != lecture_to_insert.get('id'):
                        unplaced_in_iteration.append(lec)

            elif flex_pools and random.random() <= 0.3:
                cat_id = random.choice(list(flex_pools.keys()))
                pool = flex_pools[cat_id]
                
                if len(pool['lectures']) >= 2:
                    lec1, lec2 = random.sample(pool['lectures'], 2)
                    t1_name, t2_name = lec1.get('teacher_name'), lec2.get('teacher_name')

                    if t1_name == t2_name: k+=1; continue
                    
                    swap_move_made = True
                    ids_to_remove = {lec1.get('id'), lec2.get('id')}
                    for grid in shaken_solution.values():
                        for day in grid:
                            for slot in day:
                                slot[:] = [l for l in slot if l.get('id') not in ids_to_remove]
                    
                    temp_teacher_schedule_shake.clear(); temp_room_schedule_shake.clear()
                    for grid in shaken_solution.values():
                        for d_idx, day in enumerate(grid):
                            for s_idx, lectures_in_slot in enumerate(day):
                                for lec in lectures_in_slot:
                                    temp_teacher_schedule_shake[lec['teacher_name']].add((d_idx, s_idx))
                                    if lec.get('room'): temp_room_schedule_shake[lec.get('room')].add((d_idx, s_idx))
                    
                    lec1['teacher_name'], lec2['teacher_name'] = t2_name, t1_name
                    
                    for lecture in sorted([lec1, lec2], key=lambda l: calculate_lecture_difficulty(l, updated_lectures_by_teacher_map.get(l.get('teacher_name'),[]),{},{}), reverse=True):
                        success, message = find_slot_for_single_lecture(lecture, shaken_solution, temp_teacher_schedule_shake, temp_room_schedule_shake, days, slots, rules_grid, rooms_data, teacher_constraints, globally_unavailable_slots, special_constraints, primary_slots, reserve_slots, identifiers_by_level, prioritize_primary, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule)
                        if not success: unplaced_in_iteration.append({"course_name": lecture.get('name'), "teacher_name": lecture.get('teacher_name'), "reason": message})
            else:
                lectures_to_shake_pool = [lec for lec in all_lectures if lec.get('id') in non_flexible_lecture_ids and lec.get('teacher_name')]
                if not lectures_to_shake_pool: lectures_to_shake_pool = [lec for lec in all_lectures if lec.get('teacher_name')]
                if not lectures_to_shake_pool: break
                
                num_to_shake = min(k, len(lectures_to_shake_pool))
                lectures_to_reinsert = random.sample(lectures_to_shake_pool, num_to_shake)
                
                ids_to_remove = {l.get('id') for l in lectures_to_reinsert}
                for grid in shaken_solution.values():
                    for day in grid:
                        for slot in day:
                            slot[:] = [l for l in slot if l.get('id') not in ids_to_remove]
                
                temp_teacher_schedule_shake.clear(); temp_room_schedule_shake.clear()
                for grid in shaken_solution.values():
                    for d_idx, day in enumerate(grid):
                        for s_idx, lectures in enumerate(day):
                            for lec in lectures:
                                if lec.get('teacher_name'): temp_teacher_schedule_shake[lec['teacher_name']].add((d_idx, s_idx))
                                if lec.get('room'): temp_room_schedule_shake[lec.get('room')].add((d_idx, s_idx))

                for lecture in lectures_to_reinsert:
                    success, message = find_slot_for_single_lecture(lecture, shaken_solution, temp_teacher_schedule_shake, temp_room_schedule_shake, days, slots, rules_grid, rooms_data, teacher_constraints, globally_unavailable_slots, special_constraints, primary_slots, reserve_slots, identifiers_by_level, prioritize_primary, saturday_teachers, day_to_idx, level_specific_large_rooms, specific_small_room_assignments, consecutive_large_hall_rule)
                    if not success: unplaced_in_iteration.append({"course_name": lecture.get('name'), "teacher_name": lecture.get('teacher_name'), "reason": message})

            new_cost_list = calculate_schedule_cost(shaken_solution, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, updated_lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)
            new_cost = len(new_cost_list) + (len(unplaced_in_iteration) * 1000)

            if new_cost < current_cost:
                current_solution = shaken_solution; current_cost = new_cost
                log_q.put(f' * تحسين عند الجوار k={k}. التكلفة الجديدة = {current_cost}')
                
                if swap_move_made:
                    log_q.put(f' * نجح التبديل! إعادة بناء خريطة الإسناد...')
                    updated_lectures_by_teacher_map.clear()
                    for lec in all_lectures:
                        if lec.get('teacher_name'):
                            updated_lectures_by_teacher_map[lec.get('teacher_name')].append(lec)
                
                k = 1
                if new_cost < best_cost_so_far:
                    best_cost_so_far = new_cost; best_solution_so_far = copy.deepcopy(current_solution)
                    if progress_channel: progress_channel['best_solution_so_far'] = best_solution_so_far
                    num_violations, num_unplaced = best_cost_so_far % 1000, best_cost_so_far // 1000
                    log_q.put(f' >>> إنجاز جديد! تعارضات={num_violations}, نقص={num_unplaced}')
                    progress = max(0, (10 - num_violations) / 10 * 100) if num_unplaced == 0 else 5.0
                    log_q.put(f"PROGRESS:{progress:.1f}")
            else:
                if swap_move_made:
                    lec1['teacher_name'], lec2['teacher_name'] = t1_name, t2_name
                k += 1
    
    # --- الفحص النهائي وإرجاع النتيجة ---
    
    log_q.put('انتهت خوارزمية VNS المرنة.')
    
    final_violations = calculate_schedule_cost(best_solution_so_far, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, updated_lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule)
    
    final_unplaced = []
    scheduled_ids = {lec.get('id') for grid in best_solution_so_far.values() for day in grid for slot in day for lec in slot}
    for lec in all_lectures:
        if lec.get('teacher_name') and lec.get('id') not in scheduled_ids:
            final_unplaced.append({"course_name": lec.get('name'), "teacher_name": lec.get('teacher_name'), "reason": "نقص في الحل النهائي."})
            
    final_failures = final_violations + final_unplaced
    final_cost = len(final_failures)
    
    log_q.put(f'=== انتهى VNS نهائياً - أفضل تكلفة: {final_cost} ===')
    return best_solution_so_far, final_cost, final_failures

# =====================================================================
# END: FLEXIBLE VNS ALGORITHM
# =====================================================================

# =====================================================================
# START: CLONAL SELECTION ALGORITHM (CLONALG)
# =====================================================================
def run_clonalg(log_q, all_lectures, days, slots, rooms_data, teachers, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, population_size, generations, selection_size, clone_factor, scheduling_state, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=None, initial_solution_seed=None, consecutive_large_hall_rule="none", progress_channel=None):
    
    log_q.put('--- بدء خوارزمية التحسين بالاستنساخ (CLONALG) ---')

    # 1. إنشاء مجموعة أولية من الأجسام المضادة (الحلول)
    log_q.put(f'   - جاري إنشاء مجموعة الأجسام المضادة الأولية ({population_size} حل)...')
    
    population = create_initial_population(population_size, all_lectures, days, slots, rooms_data, all_levels, level_specific_large_rooms, specific_small_room_assignments)
    time.sleep(0)

    if initial_solution_seed:
        log_q.put('   - تم دمج الحل المبدئي (الطماع) في الجيل الأول.')
        
        if population:
            population[0] = initial_solution_seed

    best_solution_so_far = None
    best_fitness_so_far = -1.0

    # 2. حلقة التطور الرئيسية
    for gen in range(generations):
        if scheduling_state.get('should_stop'):
            raise StopByUserException()
        best_cost_so_far = int(1/best_fitness_so_far - 1) if best_fitness_so_far > 0 else float('inf')
        
        log_q.put(f'--- الجيل {gen + 1}/{generations} | أفضل عدد أخطاء = {best_cost_so_far} ---')
        time.sleep(0)

        population_with_fitness = []
        for schedule in population:
            fitness, _ = calculate_fitness(schedule, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day)
            population_with_fitness.append((schedule, fitness))
        
        population_with_fitness.sort(key=lambda item: item[1], reverse=True)

        if population_with_fitness[0][1] > best_fitness_so_far:
            best_fitness_so_far = population_with_fitness[0][1]
            best_solution_so_far = copy.deepcopy(population_with_fitness[0][0])
            if progress_channel: progress_channel['best_solution_so_far'] = best_solution_so_far
            cost = int(1/best_fitness_so_far - 1)
            
            log_q.put(f'   >>> إنجاز جديد! تم الوصول إلى فشل في القيود = {cost}')
            progress_percentage = max(0, (10 - cost) / 10 * 100)
            log_q.put(f"PROGRESS:{progress_percentage:.1f}")

        if best_fitness_so_far == 1.0:
            log_q.put('   - تم العثور على حل مثالي! إنهاء البحث.')
            break

        selected_antibodies = population_with_fitness[:selection_size]
        
        cloned_and_mutated_antibodies = []
        for antibody, fitness in selected_antibodies:
            cost = (1.0/fitness - 1.0) if fitness > 0 else float('inf')
            num_clones = int( (clone_factor * selection_size) / (1 + cost) ) if fitness > 0 else 1
            num_clones = max(1, num_clones)
            
            for _ in range(num_clones):
                clone = copy.deepcopy(antibody)
                
                # ===================== بداية الكود الذي تم إصلاحه =====================
                # تم تقليل عدد الطفرات إلى طفرة واحدة فقط لجعل البحث أكثر دقة
                # وتجنب تدمير الحلول الجيدة.
                num_mutations = 1
                # ====================== نهاية الكود الذي تم إصلاحه ======================

                mutated_clone = clone
                for _ in range(num_mutations):
                    mutated_clone = mutate(mutated_clone, all_lectures, days, slots, rooms_data, 1.0)
                cloned_and_mutated_antibodies.append(mutated_clone)

        population = [item[0] for item in population_with_fitness]
        population.extend(cloned_and_mutated_antibodies)
        
        final_population_with_fitness = []
        for schedule in population:
            fitness, _ = calculate_fitness(schedule, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day)
            final_population_with_fitness.append((schedule, fitness))
        
        final_population_with_fitness.sort(key=lambda item: item[1], reverse=True)
        population = [item[0] for item in final_population_with_fitness[:population_size]]

    
    log_q.put('انتهت خوارزمية التحسين بالاستنساخ.')

    if not best_solution_so_far:
        best_solution_so_far = population_with_fitness[0][0] if population_with_fitness else create_initial_population(1, all_lectures, days, slots, rooms_data, all_levels)[0]

    _, final_cost_list = calculate_fitness(best_solution_so_far, days, slots, teachers, rooms_data, all_levels, identifiers_by_level, special_constraints, teacher_constraints, distribution_rule_type, lectures_by_teacher_map, globally_unavailable_slots, saturday_teachers, teacher_pairs, day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day)
    
    return best_solution_so_far, len(final_cost_list), final_cost_list

# =====================================================================
# END: CLONAL SELECTION ALGORITHM (CLONALG)
# =====================================================================

@app.route('/api/validate-schedule', methods=['POST'])
def validate_schedule_api():
    """
    يستقبل جدولاً كاملاً مع إعداداته ويقوم بفحصه بحثاً عن التعارضات.
    """
    data = request.get_json()
    schedule = data.get('schedule')
    settings = data.get('settings')
    days = data.get('days')
    slots = data.get('slots')

    if not all([schedule, settings, days, slots]):
        return jsonify({"error": "بيانات الفحص غير كاملة."}), 400
    
    algorithm_settings = settings.get('algorithm_settings', {})
    max_sessions_per_day_str = algorithm_settings.get('max_sessions_per_day', 'none')
    max_sessions_per_day = int(max_sessions_per_day_str) if max_sessions_per_day_str.isdigit() else None
    consecutive_large_hall_rule = algorithm_settings.get('consecutive_large_hall_rule', 'none')

    # جلب البيانات اللازمة من قاعدة البيانات
    teachers = get_teachers().get_json()
    rooms_data = get_rooms().get_json()
    all_levels = get_levels().get_json()
    
    identifiers_row = query_db('SELECT value FROM settings WHERE key = ?', ('non_repetition_identifiers',), one=True)
    identifiers_by_level = json.loads(identifiers_row['value']) if identifiers_row and identifiers_row.get('value') else {}
    
    # تحضير القيود تماماً كما في دالة الجدولة
    _, _, day_to_idx, _, rules_grid = process_schedule_structure(settings.get('schedule_structure'))
    
    teacher_constraints = {t['name']: {} for t in teachers}
    for teacher_name, days_list in settings.get('manual_days', {}).items():
        if teacher_name in teacher_constraints:
            teacher_constraints[teacher_name]['allowed_days'] = {day_to_idx[d] for d in days_list if d in day_to_idx}

    all_assigned_lectures = [c for c in get_courses().get_json() if c.get('teacher_name')]
    lectures_by_teacher_map = {}
    for lec in all_assigned_lectures:
        teacher = lec.get('teacher_name')
        lectures_by_teacher_map.setdefault(teacher, []).append(lec)

    globally_unavailable_slots = set()
    # يمكنك إضافة منطق فترات الراحة هنا إذا أردت أن يشملها الفحص
    last_slot_restrictions = settings.get('last_slot_restrictions', [])
    level_specific_large_rooms = settings.get('level_specific_large_rooms', {})
    specific_small_room_assignments = settings.get('specific_small_room_assignments', {})
    # استدعاء دالة فحص التكاليف التي تقوم بكل العمل
    conflicts = calculate_schedule_cost(
        schedule, days, slots, teachers, rooms_data, all_levels,
        identifiers_by_level, settings.get('special_constraints', {}), 
        teacher_constraints, settings.get('distribution_rule_type', 'allowed'),
        lectures_by_teacher_map, globally_unavailable_slots, 
        settings.get('saturday_teachers', []), 
        [], # teacher_pairs - يمكن تركه فارغًا لأن الفحص الأساسي يغطيه
        day_to_idx, rules_grid, last_slot_restrictions, level_specific_large_rooms, specific_small_room_assignments, max_sessions_per_day=max_sessions_per_day, consecutive_large_hall_rule=consecutive_large_hall_rule
    )
    
    return jsonify(conflicts)

@app.route('/api/comprehensive-check', methods=['POST'])
def comprehensive_check_api():
    """
    تقوم بفحص شامل للجدول يتضمن:
    1. المواد التي لم يتم جدولتها (الناقصة).
    2. تعارضات الأساتذة والقاعات (مكررين في نفس الوقت).
    """
    try:
        data = request.get_json()
        schedule = data.get('schedule')
        settings = data.get('settings')

        if not schedule or not settings:
            return jsonify({"error": "بيانات الفحص الشامل غير كاملة."}), 400
        
        algorithm_settings = settings.get('algorithm_settings', {})
        max_sessions_per_day_str = algorithm_settings.get('max_sessions_per_day', 'none')
        max_sessions_per_day = int(max_sessions_per_day_str) if max_sessions_per_day_str.isdigit() else None
        consecutive_large_hall_rule = algorithm_settings.get('consecutive_large_hall_rule', 'none')

        findings = []
        
        days, slots, day_to_idx, _, rules_grid = process_schedule_structure(settings.get('schedule_structure'))
        teachers = get_teachers().get_json()
        rooms_data = get_rooms().get_json()
        all_levels = get_levels().get_json()
        all_courses = get_courses().get_json()
        
        identifiers_row = query_db('SELECT value FROM settings WHERE key = ?', ('non_repetition_identifiers',), one=True)
        identifiers_by_level = json.loads(identifiers_row['value']) if identifiers_row and identifiers_row.get('value') else {}
        
        teacher_constraints = {t['name']: {} for t in teachers}
        if 'manual_days' in settings:
            for teacher_name, days_list in settings.get('manual_days', {}).items():
                if teacher_name in teacher_constraints:
                    teacher_constraints[teacher_name]['allowed_days'] = {day_to_idx.get(d) for d in days_list if d in day_to_idx}
        
        lectures_by_teacher_map = defaultdict(list)
        for lec in [c for c in all_courses if c.get('teacher_name')]:
            lectures_by_teacher_map[lec.get('teacher_name')].append(lec)

        # === ✨ الخطوة الأولى: استخراج الإعداد الجديد من كائن settings ===
        level_specific_large_rooms = settings.get('level_specific_large_rooms', {})
        specific_small_room_assignments = settings.get('specific_small_room_assignments', {})

        # ================== المهمة الأولى: البحث عن المواد الناقصة ==================
        all_assigned_courses_from_db = [c for c in all_courses if c.get('teacher_name')]
        all_course_ids_in_db = {c['id'] for c in all_assigned_courses_from_db}

        all_course_ids_in_schedule = set()
        if isinstance(schedule, dict):
            for level_grid in schedule.values():
                for day_list in level_grid:
                    for slot_list in day_list:
                        for lecture in slot_list:
                            if 'id' in lecture:
                                all_course_ids_in_schedule.add(lecture['id'])
        
        missing_course_ids = all_course_ids_in_db - all_course_ids_in_schedule
        
        if missing_course_ids:
            missing_courses_details = [c for c in all_assigned_courses_from_db if c['id'] in missing_course_ids]
            for missing_course in missing_courses_details:
                findings.append({
                    "type": "missing",
                    "course_name": missing_course.get('name', 'غير معروف'),
                    "teacher_name": missing_course.get('teacher_name', 'غير معروف'),
                    "reason": "المادة لم يتم إدراجها في الجدول."
                })

        # ================== المهمة الثانية والثالثة: البحث عن التكرار ==================
        # استدعاء دالة الفحص مع تمرير كل البيانات التي تم تحميلها
        conflicts = calculate_schedule_cost(
            schedule=schedule,
            days=days,
            slots=slots,
            teachers=teachers,
            rooms_data=rooms_data,
            levels=all_levels,
            identifiers_by_level=identifiers_by_level,
            special_constraints=settings.get('special_constraints', {}),
            teacher_constraints=teacher_constraints,
            distribution_rule_type=settings.get('distribution_rule_type', 'allowed'),
            lectures_by_teacher_map=lectures_by_teacher_map,
            globally_unavailable_slots=set(),
            saturday_teachers=settings.get('saturday_teachers', []),
            teacher_pairs=[],
            day_to_idx=day_to_idx,
            rules_grid=rules_grid,
            last_slot_restrictions=settings.get('last_slot_restrictions', {}),
            level_specific_large_rooms=level_specific_large_rooms,
            # === ✨ الخطوة الثانية: تمرير المعامل الجديد هنا ===
            specific_small_room_assignments=specific_small_room_assignments,
            max_sessions_per_day=max_sessions_per_day,
            consecutive_large_hall_rule=consecutive_large_hall_rule
        )

        for conflict in conflicts:
            reason = conflict.get('reason', '')
            if 'تعارض الأستاذ' in reason:
                conflict['type'] = 'duplicate_teacher'
                findings.append(conflict)
            elif 'تعارض في القاعة' in reason:
                conflict['type'] = 'duplicate_room'
                findings.append(conflict)

        return jsonify(findings)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"حدث خطأ في الفحص الشامل: {str(e)}"}), 500

@app.route('/stream-logs')
def stream_logs():
    def generate():
        while True:
            message = log_queue.get()
            if "DONE" in message: 
                yield f"data: {message}\n\n"
                break
            yield f"data: {message}\n\n"
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

# --- إضافة جديدة: دالة لإيقاف الخادم ---
@app.route('/shutdown', methods=['POST'])
def shutdown():
    def do_shutdown():
        # نستخدم مكتبة time العادية بدلاً من socketio.sleep
        time.sleep(1) 
        os.kill(os.getpid(), signal.SIGINT)

    # نستخدم مكتبة threading لبدء المهمة في الخلفية
    threading.Thread(target=do_shutdown).start()

    return jsonify({"success": True, "message": "تم إرسال إشارة إيقاف الخادم."})

# ================== الجزء الخامس: التشغيل ==================
def open_browser():
      webbrowser.open_new('http://127.0.0.1:5000/')

if __name__ == '__main__':
    # --- بداية التعديل ---
    # إنشاء سياق تطبيق يدويًا لتهيئة قاعدة البيانات
    with app.app_context():
        init_db()
    # --- نهاية التعديل ---
    
    # فتح المتصفح تلقائيا
    threading.Timer(1.5, open_browser).start()
    # استخدام خادم الإنتاج Waitress بدلاً من خادم التطوير
    serve(app, host='127.0.0.1', port=5000)