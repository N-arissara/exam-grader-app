import os
import time
import re
import pandas as pd
from flask import Flask, request, render_template, url_for, redirect
from werkzeug.utils import secure_filename
from helpers import split_pdf_and_identify_students, grade_batch_for_one_part

# --- การตั้งค่า ---
# ในการใช้งานจริง เราจะดึง Key มาจาก Environment Variables เพื่อความปลอดภัย
# แต่เพื่อความง่ายในการทดสอบบนเครื่อง สามารถใส่ Key ตรงนี้ก่อนได้
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', 'YOUR_GEMINI_API_KEY') 

app = Flask(__name__)
os.makedirs("temp_uploads", exist_ok=True) 
app.config['SESSIONS'] = {}  # ใช้เก็บข้อมูลเซสชั่นชั่วคราว

def extract_parts_from_structure(structure):
    return re.findall(r'^(Part .*?):', structure, re.MULTILINE | re.IGNORECASE)

# --- Routes (เส้นทางของเว็บ) ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/preprocess_and_setup', methods=['POST'])
def preprocess_and_setup():
    try:
        session_name = request.form['session_name']
        exam_structure = request.form['exam_structure']
        roster_df = pd.read_csv(request.files['roster_file'].stream)
        
        students_data = split_pdf_and_identify_students(
            request.files['batch_file'],
            int(request.form['pages_per_student']),
            roster_df,
            GEMINI_API_KEY
        )
        
        session_id = str(int(time.time()))
        app.config['SESSIONS'][session_id] = {
            "session_id": session_id, "session_name": session_name,
            "subject": request.form['subject'], "exam_structure": exam_structure,
            "parts": extract_parts_from_structure(exam_structure),
            "students": students_data
        }
        return redirect(url_for('dashboard', session_id=session_id))
    except Exception as e:
        return f"<h1>เกิดข้อผิดพลาด: {e}</h1>"


@app.route('/dashboard/<session_id>')
def dashboard(session_id):
    session_data = app.config['SESSIONS'].get(session_id)
    if not session_data: return "Session not found", 404
    return render_template('dashboard.html', session_data=session_data)

@app.route('/batch_grade_part', methods=['POST'])
def batch_grade_part():
    session_id = request.form['session_id']
    part_to_grade = request.form['part_to_grade']
    session_data = app.config['SESSIONS'].get(session_id)
    
    updated_students_data = grade_batch_for_one_part(
        session_data['students'], session_data['exam_structure'],
        part_to_grade, session_data['subject'], GEMINI_API_KEY
    )
    session_data['students'] = updated_students_data
    return redirect(url_for('verify_part', session_id=session_id, part_to_verify=part_to_grade))

@app.route('/verify_part/<session_id>/<part_to_verify>')
def verify_part(session_id, part_to_verify):
    session_data = app.config['SESSIONS'].get(session_id)
    return render_template('verify_part.html', session_id=session_id,
                           part_to_verify=part_to_verify, students=session_data['students'])

@app.route('/verify_student/<session_id>/<student_id>/<part_to_verify>')
def verify_student(session_id, student_id, part_to_verify):
    session_data = app.config['SESSIONS'].get(session_id)
    student_to_verify = next((s for s in session_data['students'] if str(s['id']) == str(student_id)), None)
    return render_template('verify_student.html', session_id=session_id,
                           part_to_verify=part_to_verify, student=student_to_verify)


@app.route('/save_scores', methods=['POST'])
def save_scores():
    session_id = request.form['session_id']
    part_graded = request.form['part_graded']
    session_data = app.config['SESSIONS'].get(session_id)

    for student in session_data['students']:
        new_score = request.form.get(f'score_{student["id"]}')
        if new_score is not None:
            student['scores'][part_graded]['score'] = float(new_score)
            student['scores'][part_graded]['verified'] = True
    
    print(f"Scores for Part '{part_graded}' saved for session '{session_id}'.")
    return redirect(url_for('dashboard', session_id=session_id))

@app.route('/export_csv/<session_id>')
def export_csv(session_id):
    # นี่คือส่วนที่สำคัญสำหรับ Google Drive บนเครื่อง local
    # ในการ Deploy จริง เราอาจจะต้องเปลี่ยนวิธีบันทึกไฟล์
    # แต่ตอนนี้เราจะยังไม่แก้ส่วนนี้ เพื่อให้ทดสอบได้
    drive_path = os.path.expanduser('~/Google Drive/ExamGrade_Exports') # สมมติว่า Google Drive อยู่ใน home directory
    if not os.path.exists(drive_path):
        drive_path = 'exports' # ถ้าไม่เจอ ให้ save ที่โฟลเดอร์ exports แทน
        os.makedirs(drive_path, exist_ok=True)

    session_data = app.config['SESSIONS'].get(session_id)
    if not session_data: return "Session not found", 404

    records = []
    # ( ... โค้ดสร้าง DataFrame เหมือนเดิม ... )
    for student in session_data['students']:
        row = {'student_id': student.get('id', ''), 'student_name': student.get('name', 'Unknown')}
        total_score = 0
        for part in session_data['parts']:
            score = student['scores'].get(part, {}).get('score', 0)
            row[part] = score
            total_score += score
        row['total_score'] = total_score
        records.append(row)
    df = pd.DataFrame(records)

    try:
        safe_name = secure_filename(session_data['session_name']).replace('_', ' ').strip()
        filename = f"Results_{safe_name}.csv"
        file_path = os.path.join(drive_path, filename)
        df.to_csv(file_path, index=False, encoding='utf-8-sig')
        print(f"Successfully exported scores to: {file_path}")
        return redirect(url_for('export_success', session_id=session_id, file_path=file_path))
    except Exception as e:
        return f"<h1>เกิดข้อผิดพลาดระหว่างการ Export: {e}</h1>"


@app.route('/export_success/<session_id>')
def export_success(session_id):
    file_path = request.args.get('file_path')
    return render_template('export_success.html', session_id=session_id, file_path=file_path)


if __name__ == '__main__':
    app.run(debug=True)