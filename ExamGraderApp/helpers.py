# helpers.py

# (import statements อยู่ด้านบนสุดเหมือนเดิม)
import google.generativeai as genai
import io
import json
import base64
import fitz  # PyMuPDF
from PIL import Image
import os
from werkzeug.utils import secure_filename


# --- ส่วนที่ 1: ฟังก์ชันระบุตัวตนนักเรียน (เวอร์ชันแก้ไขสมบูรณ์) ---
def split_pdf_and_identify_students(pdf_file_storage, pages_per_student, roster_df, api_key):
    """แยกไฟล์ PDF, ใช้ AI ดึงข้อมูล, และจับคู่กับรายชื่อใน Roster"""
    genai.configure(api_key=api_key)
    
    # 1. สร้าง Path ชั่วคราวเพื่อบันทึกไฟล์ PDF
    temp_dir = "temp_uploads"
    os.makedirs(temp_dir, exist_ok=True)
    filename = secure_filename(pdf_file_storage.filename)
    temp_filepath = os.path.join(temp_dir, filename)
    pdf_file_storage.save(temp_filepath)

    # 2. เปิดไฟล์ PDF จาก Path ที่บันทึกไว้ (ใช้ RAM น้อยกว่า)
    main_doc = fitz.open(temp_filepath)
    
    # --- โค้ดส่วนนี้ถูกลบออกไปแล้ว เพราะเป็นส่วนที่ใช้ RAM สูง ---
    # pdf_file_storage.stream.seek(0)
    # pdf_bytes = pdf_file_storage.read()
    # main_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    # ---------------------------------------------------------
    
    students_data = []
    model = genai.GenerativeModel('gemini-1.5-flash')
    roster_list_str = roster_df.to_string()
    
    # For loop ที่ใช้ประมวลผลแต่ละหน้ายังคงเหมือนเดิมทั้งหมด
    for i in range(0, main_doc.page_count, pages_per_student):
        first_page_of_chunk = main_doc.load_page(i)
        pix = first_page_of_chunk.get_pixmap(dpi=150)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        
        identification_prompt = f"""
        **MISSION:** You are an AI assistant specializing in student identification...
        ...
        """
        # (เนื้อหา prompt ทั้งหมดเหมือนเดิม)

        student_id = f"student_{i//pages_per_student + 1}"
        student_name = "Unknown"
        
        try:
            response = model.generate_content([identification_prompt, img])
            cleaned_response = response.text.strip().replace('```json', '').replace('```', '')
            identity = json.loads(cleaned_response)
            student_id = identity.get("student_id", student_id)
            student_name = identity.get("student_name", student_name)
        except Exception as e:
            print(f"Could not identify student on page {i}: {e}")

        student_pages_images = []
        for page_num in range(i, min(i + pages_per_student, main_doc.page_count)):
            page = main_doc.load_page(page_num)
            pix = page.get_pixmap(dpi=150)
            img_obj = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            buffered = io.BytesIO()
            img_obj.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
            student_pages_images.append(img_str)
            
        students_data.append({
            "id": student_id, "name": student_name,
            "images_b64": student_pages_images, "scores": {}
        })
        
    main_doc.close()
    
    # 3. ลบไฟล์ชั่วคราวทิ้งหลังใช้งานเสร็จ (เพิ่มส่วนนี้เข้ามา)
    os.remove(temp_filepath)
    
    return students_data

# --- ส่วนที่ 2: ฟังก์ชันตรวจข้อสอบ ---
def grade_batch_for_one_part(students_data, exam_structure, part_to_grade, subject, api_key):
    """ตรวจข้อสอบส่วนเดียวสำหรับนักเรียนทุกคน"""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    for student in students_data:
        # ( ... โค้ดส่วนนี้เหมือนเดิมทั้งหมด ... )
        student_images = [Image.open(io.BytesIO(base64.b64decode(b64_str))) for b64_str in student["images_b64"]]
        
        prompt_template = f"""
        **MISSION:** You are an expert University Professor for the subject: **{subject}**. Your task is to grade a student's answer for one specific part of an exam.
        **CONTEXT:** You will grade ONLY the part named: "{part_to_grade}".
        **YOUR TASK (Solve-then-Grade Chain of Thought):**
        1.  **Analyze the Question & Generate a Solution:** First, solve the problem yourself based on the exam structure.
        2.  **Analyze Student's Work:** Examine the student's answer in the provided images.
        3.  **Compare and Grade:** Compare the student's work against your solution and the rubric.
        4.  **Provide Feedback & Output JSON:** Write clear, constructive feedback and return a single, valid JSON object with score, total_score, and feedback.
        **REQUIRED JSON OUTPUT FORMAT:** ```json{{"score": <number>, "total_score": <number>, "feedback": "<string>"}}```
        ---
        **FULL EXAM STRUCTURE:**
        ```text
        {exam_structure}
        ```
        ---
        **STUDENT'S ANSWER IMAGES:**
        """
        
        prompt_parts = [prompt_template] + student_images
        
        try:
            response = model.generate_content(prompt_parts)
            cleaned_response = response.text.strip().replace('```json', '').replace('```', '')
            result = json.loads(cleaned_response)
            student["scores"][part_to_grade] = result
        except Exception as e:
            student["scores"][part_to_grade] = {"error": str(e), "score": 0, "total_score": 0}
            
    return students_data