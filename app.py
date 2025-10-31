import sqlite3
import datetime
import os

# Register adapters and converters for datetime to avoid DeprecationWarning in Python 3.12+
def adapt_datetime(ts):
    return ts.strftime("%Y-%m-%d %H:%M:%S")

def convert_datetime(s):
    return datetime.datetime.strptime(s.decode(), "%Y-%m-%d %H:%M:%S")

sqlite3.register_adapter(datetime.datetime, adapt_datetime)
sqlite3.register_converter("timestamp", convert_datetime)
# Save a new consultation for a user
def save_consultation(user_id, symptoms, diagnosis, recommendations, severity):
    conn = sqlite3.connect('medical_app.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO consultations (user_id, symptoms, diagnosis, recommendations, severity)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, symptoms, diagnosis, recommendations, severity))
    conn.commit()
    conn.close()
# Retrieve all consultations for a user
def get_user_consultations(user_id):
    conn = sqlite3.connect('medical_app.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM consultations WHERE user_id = ? ORDER BY created_at DESC
    ''', (user_id,))
    consultations = cursor.fetchall()
    conn.close()
    return consultations
import streamlit as st
import sqlite3
import hashlib
import json
import pandas as pd
from datetime import datetime, timedelta
import requests
import folium
from streamlit_folium import st_folium
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
import io
import base64
import re
import time
import threading

# Configure page
st.set_page_config(
    page_title="AEGIS HEALTH",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Database setup with enhanced tables
def init_database():
    conn = sqlite3.connect('medical_app.db')
    cursor = conn.cursor()
    
    # Users table with enhanced fields
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            age INTEGER,
            height REAL,
            weight REAL,
            bmi REAL,
            user_type TEXT DEFAULT 'patient',
            medical_id TEXT,
            specialization TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Consultations table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS consultations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            symptoms TEXT,
            diagnosis TEXT,
            recommendations TEXT,
            severity TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Medicine reminders table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS medicine_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            medicine_name TEXT NOT NULL,
            dosage TEXT,
            frequency TEXT,
            time_slots TEXT,
            start_date DATE,
            end_date DATE,
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Notifications table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,
            message TEXT,
            scheduled_time TIMESTAMP,
            sent BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Enhanced authentication functions
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hashed):
    return hash_password(password) == hashed

def create_user(username, email, password, age=None, height=None, weight=None, bmi=None, 
               user_type='patient', medical_id=None, specialization=None):
    conn = sqlite3.connect('medical_app.db')
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT INTO users (username, email, password_hash, age, height, weight, bmi, 
               user_type, medical_id, specialization) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (username, email, hash_password(password), age, height, weight, bmi, 
             user_type, medical_id, specialization)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def authenticate_user(username, password):
    conn = sqlite3.connect('medical_app.db')
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT id, username, email, password_hash, user_type, medical_id, specialization FROM users WHERE username = ?",
        (username,)
    )
    user = cursor.fetchone()
    conn.close()
    
    if user and verify_password(password, user[3]):
        return {
            "id": user[0], 
            "username": user[1], 
            "email": user[2], 
            "user_type": user[4],
            "medical_id": user[5],
            "specialization": user[6]
        }
    return None

# Medicine reminder functions
def add_medicine_reminder(user_id, medicine_name, dosage, frequency, time_slots, start_date, end_date):
    conn = sqlite3.connect('medical_app.db')
    cursor = conn.cursor()
    
    cursor.execute(
        """INSERT INTO medicine_reminders (user_id, medicine_name, dosage, frequency, 
           time_slots, start_date, end_date) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, medicine_name, dosage, frequency, json.dumps(time_slots), start_date, end_date)
    )
    conn.commit()
    conn.close()

def get_user_reminders(user_id):
    conn = sqlite3.connect('medical_app.db')
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT * FROM medicine_reminders WHERE user_id = ? AND active = TRUE ORDER BY created_at DESC",
        (user_id,)
    )
    reminders = cursor.fetchall()
    conn.close()
    
    return reminders

def create_water_reminder(user_id, frequency_hours=2):
    """Create daily water reminders"""
    conn = sqlite3.connect('medical_app.db')
    cursor = conn.cursor()
    
    # Clear existing water reminders for today
    today = datetime.now().date()
    cursor.execute(
        "DELETE FROM notifications WHERE user_id = ? AND type = 'water' AND DATE(scheduled_time) = ?",
        (user_id, today)
    )
    
    # Create water reminders every 2 hours from 8 AM to 10 PM
    start_hour = 8
    end_hour = 22
    
    for hour in range(start_hour, end_hour + 1, frequency_hours):
        reminder_time = datetime.combine(today, datetime.min.time().replace(hour=hour))
        cursor.execute(
            """INSERT INTO notifications (user_id, type, message, scheduled_time) 
               VALUES (?, ?, ?, ?)""",
            (user_id, 'water', '💧 Time to drink water! Stay hydrated for better health.', reminder_time)
        )
    
    conn.commit()
    conn.close()

# Enhanced map function with fixed rendering
@st.cache_data(show_spinner=False)
def get_hospital_map(hospitals, center):
    # Create map with stable tile layer
    # Calculate the average latitude and longitude for better centering
    if hospitals:
        avg_lat = sum(h['lat'] for h in hospitals) / len(hospitals)
        avg_lng = sum(h['lng'] for h in hospitals) / len(hospitals)
        map_center = [avg_lat, avg_lng]
    else:
        map_center = center
    m = folium.Map(
        location=map_center,
        zoom_start=13,
        tiles='OpenStreetMap',
        prefer_canvas=True
    )
    
    for hospital in hospitals:
        popup_text = f"""
        <div style="width: 200px;">
        <b>{hospital['name']}</b><br>
        <i class="fa fa-map-marker"></i> {hospital['address']}<br>
        <i class="fa fa-phone"></i> {hospital['phone']}<br>
        <i class="fa fa-star"></i> Rating: {hospital['rating']}/5<br>
        <i class="fa fa-ambulance"></i> Emergency: {'Yes' if hospital['emergency'] else 'No'}
        </div>
        """
        
        folium.Marker(
            [hospital['lat'], hospital['lng']],
            popup=folium.Popup(popup_text, max_width=300),
            tooltip=hospital['name'],
            icon=folium.Icon(
                color='red' if hospital['emergency'] else 'blue', 
                icon='plus',
                prefix='fa'
            )
        ).add_to(m)
    
    return m

# Enhanced medical diagnosis with more comprehensive analysis
def analyze_symptoms(symptoms_text, user_type='patient'):
    """Enhanced symptom analysis with user type consideration"""
    symptoms_lower = symptoms_text.lower()
    
    # Emergency conditions
    emergency_keywords = [
        'chest pain', 'difficulty breathing', 'severe headache', 'stroke', 'heart attack', 
        'unconscious', 'severe bleeding', 'poisoning', 'overdose', 'seizure',
        'anaphylaxis', 'severe allergic reaction', 'choking', 'cardiac arrest'
    ]
    
    # High-risk conditions
    high_risk_keywords = [
        'fever over 103', 'persistent vomiting', 'severe abdominal pain', 
        'difficulty swallowing', 'severe dehydration', 'diabetic emergency',
        'severe burns', 'head trauma', 'loss of consciousness'
    ]
    
    # Medium risk conditions
    medium_risk_keywords = [
        'moderate fever', 'persistent cough', 'shortness of breath', 'severe pain',
        'blood in stool', 'blood in urine', 'severe diarrhea', 'fainting'
    ]
    
    # Common conditions
    cold_keywords = ['runny nose', 'sneezing', 'mild fever', 'cough', 'sore throat']
    digestive_keywords = ['nausea', 'stomach pain', 'diarrhea', 'indigestion', 'heartburn']
    musculoskeletal_keywords = ['back pain', 'joint pain', 'muscle ache', 'stiffness']
    mental_health_keywords = ['anxiety', 'depression', 'stress', 'panic attack', 'insomnia']
    
    diagnosis = "General consultation needed"
    severity = "Low"
    recommendations = []
    professional_note = ""
    
    # Add professional context based on user type
    if user_type == 'medical_student':
        professional_note = "\n📚 Educational Context: Consider differential diagnoses and evidence-based treatment protocols."
    elif user_type == 'healthcare_professional':
        professional_note = "\n👩‍⚕️ Professional Assessment: Review clinical guidelines and consider patient comorbidities."
    
    # Check for emergency conditions
    if any(keyword in symptoms_lower for keyword in emergency_keywords):
        diagnosis = "⚠️ EMERGENCY CONDITION DETECTED"
        severity = "CRITICAL"
        recommendations = [
            "🚨 CALL 911 IMMEDIATELY",
            "Go to the nearest emergency room",
            "Do not drive yourself - call ambulance",
            "Have someone stay with you",
            "Prepare list of current medications",
            "Stay calm and follow emergency operator instructions"
        ]
    
    # Check for high-risk conditions
    elif any(keyword in symptoms_lower for keyword in high_risk_keywords):
        diagnosis = "High-risk condition - Urgent medical attention needed"
        severity = "High"
        recommendations = [
            "Seek immediate medical attention within 2-4 hours",
            "Visit urgent care or emergency room",
            "Contact your primary care physician immediately",
            "Monitor symptoms closely and call 911 if worsening",
            "Avoid eating or drinking until medical evaluation",
            "Have someone available to drive you to medical facility"
        ]
    
    # Check for medium-risk conditions
    elif any(keyword in symptoms_lower for keyword in medium_risk_keywords):
        diagnosis = "Moderate concern - Medical evaluation recommended within 24 hours"
        severity = "Medium"
        recommendations = [
            "Schedule appointment with healthcare provider within 24 hours",
            "Monitor symptoms and seek urgent care if worsening",
            "Take temperature regularly and keep symptom log",
            "Stay hydrated and rest",
            "Avoid strenuous activities"
        ]
    
    # Check for common conditions
    elif any(keyword in symptoms_lower for keyword in cold_keywords):
        diagnosis = "Possible common cold or upper respiratory infection"
        severity = "Low"
        recommendations = [
            "Rest and stay well hydrated with warm fluids",
            "Use over-the-counter medications as directed",
            "Gargle with warm salt water for sore throat",
            "Use humidifier to ease congestion",
            "See a doctor if symptoms worsen or persist beyond 7-10 days",
            "Isolate to prevent spreading to others"
        ]
    
    elif any(keyword in symptoms_lower for keyword in digestive_keywords):
        diagnosis = "Possible digestive issue or gastroenteritis"
        severity = "Low"
        recommendations = [
            "Stay hydrated with clear fluids and electrolyte solutions",
            "Follow BRAT diet (bananas, rice, applesauce, toast)",
            "Avoid dairy, caffeine, alcohol, and fatty foods",
            "Rest and allow digestive system to recover",
            "See a doctor if symptoms persist beyond 48 hours",
            "Seek immediate care if signs of severe dehydration appear"
        ]
    
    elif any(keyword in symptoms_lower for keyword in musculoskeletal_keywords):
        diagnosis = "Possible musculoskeletal condition or injury"
        severity = "Low"
        recommendations = [
            "Apply RICE protocol: Rest, Ice, Compression, Elevation",
            "Use over-the-counter anti-inflammatory medications as directed",
            "Gentle stretching and movement as tolerated",
            "Heat therapy after initial 48 hours if helpful",
            "See a doctor if pain is severe or persists beyond a week",
            "Physical therapy may be beneficial for chronic issues"
        ]
    
    elif any(keyword in symptoms_lower for keyword in mental_health_keywords):
        diagnosis = "Possible mental health concern"
        severity = "Medium"
        recommendations = [
            "Consider speaking with a mental health professional",
            "Practice stress reduction techniques (meditation, deep breathing)",
            "Maintain regular sleep schedule and healthy diet",
            "Stay connected with supportive friends and family",
            "Contact crisis helpline if having thoughts of self-harm: 988",
            "Regular exercise can help improve mood and reduce anxiety"
        ]
    
    return diagnosis, severity, recommendations, professional_note

# Enhanced chatbot with voice capability placeholder
def medical_chatbot_response(question, user_type='patient'):
    """Enhanced medical chatbot with user type consideration"""
    question_lower = question.lower()
    
    response_prefix = ""
    if user_type == 'medical_student':
        response_prefix = "📚 **Educational Response:** "
    elif user_type == 'healthcare_professional':
        response_prefix = "👩‍⚕️ **Professional Insight:** "
    
    if any(word in question_lower for word in ['fever', 'temperature']):
        return f"""{response_prefix}🌡️ **About Fever:**
        
A fever is generally considered a temperature above 100.4°F (38°C). Here's comprehensive information:

**Pathophysiology:** Fever is the body's natural immune response to infection or inflammation, mediated by pyrogens affecting the hypothalamic thermostat.

**Assessment Guidelines:**
- Low-grade: 100.4-102°F (38-38.9°C)
- Moderate: 102-104°F (38.9-40°C)  
- High-grade: >104°F (>40°C)

**When to seek immediate care:**
- Temperature above 103°F (39.4°C)
- Fever lasting more than 3 days
- Accompanied by severe symptoms (difficulty breathing, chest pain, severe headache)
- Febrile seizures (especially in children)

**Evidence-based management:**
- Maintain hydration (increase fluid intake by 15-20%)
- Antipyretics: Acetaminophen 650-1000mg q6h or Ibuprofen 400-600mg q6h
- Cool compresses to forehead and wrists
- Rest in cool environment

**Red flags requiring immediate evaluation:**
- Petechial rash, nuchal rigidity, altered mental status, severe dehydration"""
    
    elif any(word in question_lower for word in ['headache', 'head pain']):
        return f"""{response_prefix}🤕 **About Headaches:**
        
**Classification (IHS Criteria):**
- Primary: Tension-type (90%), Migraine, Cluster
- Secondary: Due to underlying pathology

**Differential Diagnosis:**
- Tension headaches: Bilateral, pressing/tightening quality
- Migraines: Unilateral, pulsating, with nausea/photophobia
- Cluster: Severe unilateral periorbital pain
- Secondary: SAH, meningitis, temporal arteritis

**Red flag symptoms (require immediate evaluation):**
- Sudden onset "thunderclap" headache
- Headache with fever, neck stiffness, altered consciousness
- New headache in patient >50 years
- Progressive worsening pattern
- Headache following head trauma

**Management approach:**
- Acute: NSAIDs, triptans (for migraines), avoid medication overuse
- Prophylaxis: Consider for >4 headache days/month
- Non-pharmacological: Sleep hygiene, stress management, trigger avoidance"""
    
    elif any(word in question_lower for word in ['chest pain', 'cardiac', 'heart']):
        return f"""{response_prefix}❤️ **About Chest Pain:**
        
**⚠️ CRITICAL: Chest pain requires immediate professional evaluation**

**High-risk features (ACS indicators):**
- Crushing, pressure-like substernal pain
- Radiation to left arm, jaw, or back
- Associated with diaphoresis, nausea, dyspnea
- Worse with exertion, better with rest

**Differential diagnosis:**
- Cardiac: ACS, pericarditis, aortic dissection
- Pulmonary: PE, pneumothorax, pneumonia
- GI: GERD, esophageal spasm
- Musculoskeletal: Costochondritis, muscle strain

**Immediate actions:**
1. Call 911 if suspected cardiac etiology
2. Administer aspirin 325mg (if no contraindications)
3. Position patient comfortably
4. Monitor vital signs
5. Prepare for potential CPR

**Never ignore chest pain - early intervention saves lives**"""
    
    elif any(word in question_lower for word in ['mental health', 'depression', 'anxiety']):
        return f"""{response_prefix}🧠 **About Mental Health:**
        
**Screening tools:**
- PHQ-9 for depression screening
- GAD-7 for anxiety assessment
- Suicide risk assessment (PHQ-9 item 9)

**Evidence-based treatments:**
- Depression: CBT, IPT, SSRIs, SNRIs
- Anxiety: CBT, exposure therapy, SSRIs, benzodiazepines (short-term)
- Combined approach often most effective

**Crisis resources:**
- National Suicide Prevention Lifeline: 988
- Crisis Text Line: Text HOME to 741741
- Emergency services: 911

**Professional referral indicators:**
- Persistent symptoms >2 weeks
- Functional impairment
- Suicidal ideation
- Substance abuse comorbidity

**Lifestyle interventions:**
- Regular exercise (30 min, 5x/week)
- Sleep hygiene (7-9 hours)
- Mindfulness/meditation practices
- Social connection and support"""
    
    else:
        return f"""{response_prefix}🩺 **Comprehensive Health Information:**
        
I provide evidence-based medical information tailored to your professional level:

**For Medical Students:** Focus on pathophysiology, differential diagnosis, and learning objectives
**For Healthcare Professionals:** Clinical pearls, recent guidelines, and practice management
**For Patients:** Clear, actionable health guidance and when to seek care

**Available topics:**
- Symptom assessment and triage
- Medication information and interactions  
- Diagnostic criteria and clinical guidelines
- Emergency recognition and management
- Preventive health measures
- Mental health screening and support

**Quality assurance:**
- Information based on current medical literature
- Guidelines from major medical organizations
- Regular updates with latest evidence

Please ask about specific symptoms, conditions, or health topics for detailed, personalized responses."""

# Enhanced hospital finder with more realistic data
def find_nearby_hospitals(city, state):
    """Return Tamil Nadu hospital data for demo purposes"""
    tamil_nadu_hospitals = [
        {
            'name': 'Apollo Hospital Chennai',
            'address': '21, Greams Lane, Off Greams Road, Chennai, Tamil Nadu',
            'phone': '044-2829 3333',
            'rating': 4.7,
            'emergency': True,
            'specialties': ['Emergency Medicine', 'Cardiology', 'Neurology', 'Trauma'],
            'beds': 500,
            'lat': 13.0632,
            'lng': 80.2618
        },
        {
            'name': 'Government General Hospital',
            'address': 'Poonamallee High Rd, Park Town, Chennai, Tamil Nadu',
            'phone': '044-2530 5000',
            'rating': 4.2,
            'emergency': True,
            'specialties': ['Emergency Medicine', 'Surgery', 'Pediatrics', 'Orthopedics'],
            'beds': 1200,
            'lat': 13.0827,
            'lng': 80.2707
        },
        {
            'name': 'Kauvery Hospital',
            'address': '199, Luz Church Road, Mylapore, Chennai, Tamil Nadu',
            'phone': '044-4000 6000',
            'rating': 4.5,
            'emergency': True,
            'specialties': ['Cardiology', 'General Medicine', 'Orthopedics'],
            'beds': 300,
            'lat': 13.0337,
            'lng': 80.2549
        },
        {
            'name': 'MIOT International',
            'address': '4/112, Mount Poonamallee Road, Manapakkam, Chennai, Tamil Nadu',
            'phone': '044-4200 2288',
            'rating': 4.6,
            'emergency': True,
            'specialties': ['Emergency Medicine', 'Cardiology', 'Orthopedics'],
            'beds': 1000,
            'lat': 13.0107,
            'lng': 80.1802
        },
        {
            'name': 'Fortis Malar Hospital',
            'address': '52, 1st Main Road, Gandhi Nagar, Adyar, Chennai, Tamil Nadu',
            'phone': '044-4289 2222',
            'rating': 4.3,
            'emergency': True,
            'specialties': ['Emergency Medicine', 'Cardiology', 'Neurology'],
            'beds': 180,
            'lat': 13.0067,
            'lng': 80.2570
        }
    ]
    return tamil_nadu_hospitals

# Enhanced PDF generation
def generate_pdf_report(consultation_data, user_info):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5*inch)
    styles = getSampleStyleSheet()
    story = []
    
    # Enhanced title with logo placeholder
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=20,
        spaceAfter=30,
        textColor=colors.HexColor('#2E86AB'),
        alignment=1  # Center alignment
    )
    story.append(Paragraph("🩺 AEGIS HEALTH", title_style))
    story.append(Paragraph("Comprehensive Medical Consultation Report", styles['Heading2']))
    story.append(Spacer(1, 20))
    
    # Patient information table
    patient_data = [
        ['Patient Information', ''],
        ['Username:', user_info['username']],
        ['User Type:', user_info.get('user_type', 'Patient').title()],
        ['Date of Consultation:', consultation_data['date']],
        ['Report Generated:', datetime.now().strftime('%Y-%m-%d %H:%M:%S')]
    ]
    
    if user_info.get('medical_id'):
        patient_data.append(['Medical ID:', user_info['medical_id']])
    if user_info.get('specialization'):
        patient_data.append(['Specialization:', user_info['specialization']])
    
    patient_table = Table(patient_data, colWidths=[2*inch, 4*inch])
    patient_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2E86AB')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    story.append(patient_table)
    story.append(Spacer(1, 20))
    
    # Rest of the report content...
    # [Previous PDF content with enhancements]
    
    doc.build(story)
    buffer.seek(0)
    return buffer

def main():
    init_database()
    
    # Enhanced CSS with modern design
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    
    * {
        font-family: 'Inter', sans-serif;
    }
    
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 2rem;
        border-radius: 20px;
        color: white;
        text-align: center;
        margin-bottom: 2rem;
        box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        position: relative;
        overflow: hidden;
    }
    
    .main-header::before {
        content: '';
        position: absolute;
        top: -50%;
        left: -50%;
        width: 200%;
        height: 200%;
        background: linear-gradient(45deg, transparent, rgba(255,255,255,0.1), transparent);
        transform: rotate(45deg);
        animation: shine 3s infinite;
    }
    
    @keyframes shine {
        0% { transform: translateX(-100%) translateY(-100%) rotate(45deg); }
        100% { transform: translateX(100%) translateY(100%) rotate(45deg); }
    }
    
    .hero-quote {
        background: linear-gradient(135deg, #ff6b6b 0%, #ff8e53 100%);
        padding: 2rem;
        border-radius: 15px;
        color: white;
        text-align: center;
        margin: 2rem 0;
        box-shadow: 0 8px 25px rgba(0,0,0,0.15);
        border-left: 5px solid rgba(255,255,255,0.3);
    }
    
    .metric-card {
        background: white;
        padding: 1.5rem;
        border-radius: 15px;
        box-shadow: 0 5px 15px rgba(0,0,0,0.08);
        border-left: 4px solid #667eea;
        transition: transform 0.3s ease, box-shadow 0.3s ease;
    }
    
    .metric-card:hover {
        transform: translateY(-5px);
        box-shadow: 0 8px 25px rgba(0,0,0,0.15);
    }
    
    .emergency-alert {
        background: linear-gradient(135deg, #ff4757 0%, #ff3742 100%);
        color: white;
        padding: 1.5rem;
        border-radius: 15px;
        margin: 1rem 0;
        animation: pulse 2s infinite;
        border: 3px solid rgba(255,255,255,0.3);
    }
    
    @keyframes pulse {
        0% { box-shadow: 0 0 0 0 rgba(255, 71, 87, 0.4); }
        70% { box-shadow: 0 0 0 10px rgba(255, 71, 87, 0); }
        100% { box-shadow: 0 0 0 0 rgba(255, 71, 87, 0); }
    }
    
    .success-alert {
        background: linear-gradient(135deg, #2ed573 0%, #1e90ff 100%);
        color: white;
        padding: 1.5rem;
        border-radius: 15px;
        margin: 1rem 0;
        border-left: 5px solid rgba(255,255,255,0.3);
    }
    
    .user-type-badge {
        display: inline-block;
        padding: 0.5rem 1rem;
        border-radius: 20px;
        font-size: 0.85rem;
        font-weight: 600;
        margin: 0.5rem;
    }
    
    .patient-badge { background: linear-gradient(135deg, #74b9ff 0%, #0984e3 100%); color: white; }
    .student-badge { background: linear-gradient(135deg, #55a3ff 0%, #003d82 100%); color: white; }
    .professional-badge { background: linear-gradient(135deg, #fd79a8 0%, #e84393 100%); color: white; }
    
    .stButton > button {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        border-radius: 10px;
        padding: 0.75rem 1.5rem;
        font-weight: 600;
        transition: all 0.3s ease;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
    }
    
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(0,0,0,0.15);
    }
    
    .notification-card {
        background: linear-gradient(135deg, #ffeaa7 0%, #fdcb6e 100%);
        padding: 1rem;
        border-radius: 10px;
        margin: 0.5rem 0;
        border-left: 4px solid #e17055;
    }
    
    .voice-controls {
        background: rgba(255,255,255,0.1);
        padding: 1rem;
        border-radius: 10px;
        margin: 1rem 0;
        backdrop-filter: blur(10px);
    }
    
    .map-container {
        border-radius: 15px;
        overflow: hidden;
        box-shadow: 0 8px 25px rgba(0,0,0,0.1);
    }
    </style>
    """, unsafe_allow_html=True)
    
    # Header with enhanced quote
    st.markdown("""
    <div class="main-header">
        <h1 style="font-size: 3rem; margin-bottom: 1rem;">🩺 AEGIS HEALTH</h1>
        <p style="font-size: 1.2rem; opacity: 0.9;">Your Intelligent Medical Companion</p>
    </div>
    """, unsafe_allow_html=True)
    
    # Hero Quote Section
    st.markdown("""
    <div class="hero-quote">
        <h2 style="margin-bottom: 1rem; font-size: 1.8rem;">💫 "Health is not valued until sickness comes" 💫</h2>
        <p style="font-size: 1.1rem; opacity: 0.95; line-height: 1.6;">
            Empowering medical students, healthcare professionals, and patients with AI-driven insights for better health outcomes. 
            Your journey to wellness starts with informed decisions.
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    # Session state initialization
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
    if 'user' not in st.session_state:
        st.session_state.user = None
    if 'page' not in st.session_state:
        st.session_state.page = 'login'
    if 'notifications' not in st.session_state:
        st.session_state.notifications = []
    if 'voice_enabled' not in st.session_state:
        st.session_state.voice_enabled = False
    
    # Authentication pages
    if not st.session_state.logged_in:
        col1, col2, col3 = st.columns([1, 2, 1])
        
        with col2:
            tab1, tab2 = st.tabs(["🔑 Login", "📝 Sign Up"])
            
            with tab1:
                st.markdown("### Welcome Back to Your Health Journey!")
                with st.form("login_form"):
                    username = st.text_input("Username", placeholder="Enter your username")
                    password = st.text_input("Password", type="password", placeholder="Enter your password")
                    login_button = st.form_submit_button("🚀 Login", use_container_width=True)
                    
                    if login_button:
                        if username and password:
                            user = authenticate_user(username, password)
                            if user:
                                st.session_state.logged_in = True
                                st.session_state.user = user
                                # Create water reminders for the day
                                create_water_reminder(user['id'])
                                st.success("✅ Welcome back! Logging you in...")
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error("❌ Invalid credentials. Please try again.")
                        else:
                            st.warning("⚠️ Please fill in all fields")
            
            with tab2:
                st.markdown("### Join Our Healthcare Community!")
                with st.form("signup_form"):
                    new_username = st.text_input("Choose Username", placeholder="Your unique username")
                    new_email = st.text_input("Email Address", placeholder="your.email@example.com")
                    
                    # User type selection with descriptions
                    user_type = st.selectbox(
                        "I am a:",
                        options=['patient', 'medical_student', 'healthcare_professional'],
                        format_func=lambda x: {
                            'patient': '👤 Patient - Seeking health guidance',
                            'medical_student': '📚 Medical Student - Learning and practicing',
                            'healthcare_professional': '👩‍⚕️ Healthcare Professional - Clinical practice'
                        }[x]
                    )
                    
                    # Additional fields based on user type
                    medical_id = None
                    specialization = None
                    
                    if user_type == 'medical_student':
                        medical_id = st.text_input("Student ID", placeholder="Medical school ID number")
                        specialization = st.selectbox(
                            "Year of Study:",
                            ["Pre-clinical Year 1", "Pre-clinical Year 2", "Clinical Year 3", 
                             "Clinical Year 4", "Intern", "Resident"]
                        )
                    elif user_type == 'healthcare_professional':
                        medical_id = st.text_input("License/Registration Number", placeholder="Professional license number")
                        specialization = st.selectbox(
                            "Specialty:",
                            ["Family Medicine", "Internal Medicine", "Pediatrics", "Surgery", 
                             "Emergency Medicine", "Cardiology", "Neurology", "Psychiatry", 
                             "Radiology", "Anesthesiology", "Other"]
                        )
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        new_password = st.text_input("Create Password", type="password", 
                                                   placeholder="Minimum 8 characters")
                        age = st.number_input("Age", min_value=0, max_value=120, step=1, value=25)
                        height = st.number_input("Height (cm)", min_value=50, max_value=250, step=1, value=170)
                    
                    with col2:
                        confirm_password = st.text_input("Confirm Password", type="password", 
                                                       placeholder="Re-enter your password")
                        weight = st.number_input("Weight (kg)", min_value=10, max_value=300, step=1, value=70)
                        
                        bmi = 0
                        if height > 0 and weight > 0:
                            bmi = round(weight / ((height / 100) ** 2), 2)
                        
                        st.text_input("BMI (Auto-calculated)", value=str(bmi) if bmi > 0 else "", disabled=True)
                    
                    terms_agreed = st.checkbox("I agree to the Terms of Service and Privacy Policy")
                    signup_button = st.form_submit_button("🎉 Create Account", use_container_width=True)
                    
                    if signup_button:
                        if all([new_username, new_email, new_password, confirm_password, age, height, weight]) and terms_agreed:
                            if new_password == confirm_password:
                                if len(new_password) >= 8:
                                    if re.match(r"[^@]+@[^@]+\.[^@]+", new_email):
                                        # Additional validation for professionals
                                        if user_type in ['medical_student', 'healthcare_professional'] and not medical_id:
                                            st.error("Please provide your medical ID/license number")
                                        else:
                                            if create_user(new_username, new_email, new_password, age, height, 
                                                         weight, bmi, user_type, medical_id, specialization):
                                                st.success("🎉 Account created successfully! Please login to continue.")
                                                st.balloons()
                                            else:
                                                st.error("❌ Username or email already exists")
                                    else:
                                        st.error("📧 Please enter a valid email address")
                                else:
                                    st.error("🔒 Password must be at least 8 characters long")
                            else:
                                st.error("❌ Passwords don't match")
                        elif not terms_agreed:
                            st.error("📋 Please agree to the Terms of Service")
                        else:
                            st.warning("⚠️ Please fill in all required fields")
    
    # Main application (after login)
    else:
        # Enhanced sidebar with user type badge
        with st.sidebar:
            # User profile section
            user_type_colors = {
                'patient': 'patient-badge',
                'medical_student': 'student-badge', 
                'healthcare_professional': 'professional-badge'
            }
            
            user_type_icons = {
                'patient': '👤',
                'medical_student': '📚',
                'healthcare_professional': '👩‍⚕️'
            }
            
            st.markdown(f"""
            <div style="text-align: center; padding: 1rem; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 15px; margin-bottom: 1rem;">
                <h3 style="color: white; margin: 0;">Welcome back!</h3>
                <h2 style="color: white; margin: 0.5rem 0;">{st.session_state.user['username']}</h2>
                <span class="{user_type_colors.get(st.session_state.user.get('user_type', 'patient'), 'patient-badge')}">
                    {user_type_icons.get(st.session_state.user.get('user_type', 'patient'), '👤')} 
                    {st.session_state.user.get('user_type', 'patient').replace('_', ' ').title()}
                </span>
            </div>
            """, unsafe_allow_html=True)
            
            # Navigation buttons
            st.markdown("### 🧭 Navigation")
            
            nav_buttons = [
                ("🏠 Dashboard", "dashboard", "🏠"),
                ("🩺 Symptom Diagnosis", "diagnosis", "🩺"),
                ("💬 Medical Chatbot", "chatbot", "💬"),
                ("🏥 Find Hospitals", "hospitals", "🏥"),
                ("💊 Medicine Reminders", "reminders", "💊"),
                ("📊 My Reports", "reports", "📊"),
                ("🔔 Notifications", "notifications", "🔔")
            ]
            
            for button_text, page_key, icon in nav_buttons:
                if st.button(button_text, use_container_width=True, key=f"nav_{page_key}"):
                    st.session_state.page = page_key
                    st.rerun()
            
            # Quick health tips based on user type
            st.markdown("---")
            if st.session_state.user.get('user_type') == 'medical_student':
                st.markdown("""
                ### 📚 Study Tips
                - Review cases daily
                - Practice differential diagnosis
                - Join study groups
                - Use evidence-based resources
                """)
            elif st.session_state.user.get('user_type') == 'healthcare_professional':
                st.markdown("""
                ### 👩‍⚕️ Professional Resources
                - Latest clinical guidelines
                - Continuing education credits
                - Peer consultation network
                - Research updates
                """)
            else:
                st.markdown("""
                ### 💡 Health Tips
                - Stay hydrated (8 glasses/day)
                - Regular exercise (150 min/week)
                - 7-9 hours sleep
                - Balanced nutrition
                """)
            
            st.markdown("---")
            
            # Emergency contacts
            st.markdown("""
            ### 🚨 Tamil Nadu Emergency Contacts
            **Medical Helpline:** 104  
            **Ambulance:** 108  
            **Police:** 100  
            """)
            
            st.markdown("---")
            if st.button("🚪 Logout", use_container_width=True, type="secondary"):
                st.session_state.logged_in = False
                st.session_state.user = None
                st.session_state.page = 'login'
                st.session_state.notifications = []
                st.rerun()
        
        # Notification system
        def check_notifications():
            """Check for pending notifications"""
            if st.session_state.user:
                conn = sqlite3.connect('medical_app.db')
                cursor = conn.cursor()
                
                # Get pending notifications
                now = datetime.now()
                cursor.execute(
                    """SELECT * FROM notifications 
                       WHERE user_id = ? AND sent = FALSE 
                       AND scheduled_time <= ? 
                       ORDER BY scheduled_time""",
                    (st.session_state.user['id'], now)
                )
                notifications = cursor.fetchall()
                
                # Mark as sent and add to session state
                for notification in notifications:
                    cursor.execute(
                        "UPDATE notifications SET sent = TRUE WHERE id = ?",
                        (notification[0],)
                    )
                    st.session_state.notifications.append({
                        'id': notification[0],
                        'type': notification[2],
                        'message': notification[3],
                        'time': notification[4]
                    })
                
                conn.commit()
                conn.close()
        
        # Check for notifications
        check_notifications()
        
        # Display notifications
        if st.session_state.notifications:
            for notification in st.session_state.notifications[-3:]:  # Show last 3
                st.markdown(f"""
                <div class="notification-card">
                    <strong>{notification['message']}</strong>
                    <br><small>⏰ {notification['time']}</small>
                </div>
                """, unsafe_allow_html=True)
        
        # Main content area based on selected page
        if
            'diagnosis', 'chatbot', 'hospitals', 'reports', 'reminders', 'notifications'
        ]:
            # Enhanced Dashboard
            st.markdown("# 📊 Personal Health Dashboard")
            
            # Quick stats
            consultations = get_user_consultations(st.session_state.user['id'])
            reminders = get_user_reminders(st.session_state.user['id'])
            
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.markdown(f"""
                <div class="metric-card">
                    <h3 style="color: #667eea; margin-bottom: 0.5rem;">📋 Total Consultations</h3>
                    <h2 style="color: #2d3436; margin: 0; font-size: 2.5rem;">{len(consultations)}</h2>
                    <p style="color: #636e72; margin: 0.5rem 0 0 0; font-size: 0.9rem;">All time record</p>
                </div>
                """, unsafe_allow_html=True)
            
            with col2:
                recent_consultations = len([c for c in consultations if 
                                          (datetime.now() - datetime.strptime(c[6], '%Y-%m-%d %H:%M:%S')).days <= 7])
                st.markdown(f"""
                <div class="metric-card">
                    <h3 style="color: #00b894; margin-bottom: 0.5rem;">🗓️ This Week</h3>
                    <h2 style="color: #2d3436; margin: 0; font-size: 2.5rem;">{recent_consultations}</h2>
                    <p style="color: #636e72; margin: 0.5rem 0 0 0; font-size: 0.9rem;">Recent activity</p>
                </div>
                """, unsafe_allow_html=True)
            
            with col3:
                st.markdown(f"""
                <div class="metric-card">
                    <h3 style="color: #fdcb6e; margin-bottom: 0.5rem;">💊 Active Reminders</h3>
                    <h2 style="color: #2d3436; margin: 0; font-size: 2.5rem;">{len(reminders)}</h2>
                    <p style="color: #636e72; margin: 0.5rem 0 0 0; font-size: 0.9rem;">Medicine schedule</p>
                </div>
                """, unsafe_allow_html=True)
            
            with col4:
                health_score = min(100, 60 + (len(consultations) * 5) + (len(reminders) * 10))
                st.markdown(f"""
                <div class="metric-card">
                    <h3 style="color: #e17055; margin-bottom: 0.5rem;">❤️ Health Score</h3>
                    <h2 style="color: #2d3436; margin: 0; font-size: 2.5rem;">{health_score}%</h2>
                    <p style="color: #636e72; margin: 0.5rem 0 0 0; font-size: 0.9rem;">Wellness index</p>
                </div>
                """, unsafe_allow_html=True)
            
            st.markdown("---")
            
            # User-specific dashboard content
            if st.session_state.user.get('user_type') == 'medical_student':
                st.markdown("## 📚 Medical Student Hub")
                
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("""
                    ### 🎯 Learning Objectives Today
                    - [ ] Review cardiovascular pathophysiology
                    - [ ] Practice physical examination techniques
                    - [ ] Study pharmacokinetics principles
                    - [ ] Complete case study analysis
                    """)
                    
                    if st.button("📖 Access Study Materials", use_container_width=True):
                        st.info("Study materials would be integrated here in a full implementation")
                
                with col2:
                    st.markdown("""
                    ### 📊 Study Progress
                    - **Cases Reviewed:** 15/50
                    - **Quiz Average:** 87%
                    - **Study Hours This Week:** 25
                    - **Next Exam:** Cardiology (5 days)
                    """)
                    
                    if st.button("📈 View Detailed Analytics", use_container_width=True):
                        st.info("Detailed study analytics would be shown here")
            
            elif st.session_state.user.get('user_type') == 'healthcare_professional':
                st.markdown("## 👩‍⚕️ Professional Dashboard")
                
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("""
                    ### 🏥 Today's Schedule
                    - **09:00 AM** - Patient Consultation
                    - **11:00 AM** - Surgical Procedure
                    - **02:00 PM** - Department Meeting  
                    - **04:00 PM** - Research Review
                    """)
                    
                    if st.button("📅 Manage Schedule", use_container_width=True):
                        st.info("Calendar integration would be available here")
                
                with col2:
                    st.markdown("""
                    ### 📋 Clinical Updates
                    - **New Guidelines:** Hypertension Management 2024
                    - **Drug Alerts:** 2 new safety warnings
                    - **Research:** 5 relevant studies published
                    - **CME Credits:** 12/25 completed
                    """)
                    
                    if st.button("🔬 View Clinical Resources", use_container_width=True):
                        st.info("Clinical resources and guidelines would be displayed")
            
            else:  # Patient dashboard
                st.markdown("## 👤 Your Health Journey")
                
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("""
                    ### 🎯 Health Goals
                    - [ ] Drink 8 glasses of water daily
                    - [ ] Exercise 30 minutes, 5x per week
                    - [ ] Take medications as prescribed
                    - [ ] Get 7-8 hours of sleep
                    """)
                    
                    if st.button("⚡ Quick Health Check", use_container_width=True):
                        st.session_state.page = 'diagnosis'
                        st.rerun()
                
                with col2:
                    st.markdown("""
                    ### 📈 Health Trends
                    - **Water Intake:** 6/8 glasses today
                    - **Sleep Quality:** Good (7.5 hrs)
                    - **Exercise:** 3/5 sessions this week
                    - **Medication Adherence:** 95%
                    """)
                    
                    if st.button("💊 Manage Medications", use_container_width=True):
                        st.session_state.page = 'reminders'
                        st.rerun()
            
            # Quick actions section
            st.markdown("---")
            st.markdown("## 🚀 Quick Actions")
            
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                if st.button("🩺 New Diagnosis", use_container_width=True):
                    st.session_state.page = 'diagnosis'
                    st.rerun()
            
            with col2:
                if st.button("💬 Ask AI Doctor", use_container_width=True):
                    st.session_state.page = 'chatbot'
                    st.rerun()
            
            with col3:
                if st.button("🏥 Find Care", use_container_width=True):
                    st.session_state.page = 'hospitals'
                    st.rerun()
            
            with col4:
                if st.button("📊 View Reports", use_container_width=True):
                    st.session_state.page = 'reports'
                    st.rerun()
            
            # Recent activity
            if consultations:
                st.markdown("---")
                st.markdown("## 📋 Recent Health Activity")
                
                for consultation in consultations[:2]:
                    severity_color = "#e74c3c" if consultation[5] == "CRITICAL" else "#f39c12" if consultation[5] == "High" else "#27ae60"
                    
                    st.markdown(f"""
                    <div style="background: white; padding: 1rem; border-radius: 10px; margin: 0.5rem 0; border-left: 4px solid {severity_color}; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                        <h4 style="margin: 0; color: #2d3436;">📅 {consultation[6][:16]}</h4>
                        <p style="margin: 0.5rem 0; color: #636e72;"><strong>Symptoms:</strong> {consultation[2][:100]}{'...' if len(consultation[2]) > 100 else ''}</p>
                        <p style="margin: 0; color: {severity_color};"><strong>Severity:</strong> {consultation[5]}</p>
                    </div>
                    """, unsafe_allow_html=True)
        
        elif st.session_state.page == 'reminders':
            st.markdown("## 💊 Medicine Reminders & Health Notifications")
            
            tab1, tab2, tab3 = st.tabs(["💊 My Medications", "➕ Add New", "🔔 Notifications"])
            
            with tab1:
                reminders = get_user_reminders(st.session_state.user['id'])
                
                if reminders:
                    st.markdown("### 📋 Your Current Medications")
                    
                    for reminder in reminders:
                        time_slots = json.loads(reminder[5]) if reminder[5] else []
                        
                        with st.expander(f"💊 {reminder[2]} - {reminder[3]}"):
                            col1, col2 = st.columns(2)
                            
                            with col1:
                                st.write(f"**Dosage:** {reminder[3]}")
                                st.write(f"**Frequency:** {reminder[4]}")
                                st.write(f"**Duration:** {reminder[6]} to {reminder[7]}")
                            
                            with col2:
                                st.write(f"**Times:** {', '.join(time_slots)}")
                                st.write(f"**Status:** {'🟢 Active' if reminder[8] else '🔴 Inactive'}")
                            
                            col1, col2 = st.columns(2)
                            with col1:
                                if st.button(f"✅ Mark as Taken", key=f"taken_{reminder[0]}"):
                                    st.success("✅ Medication marked as taken!")
                            with col2:
                                if st.button(f"❌ Deactivate", key=f"deactivate_{reminder[0]}"):
                                    # Deactivate reminder logic here
                                    st.info("Reminder deactivated")
                else:
                    st.info("📭 No active medication reminders. Add your first medication below!")
            
            with tab2:
                st.markdown("### ➕ Add New Medication Reminder")
                
                with st.form("add_reminder"):
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        medicine_name = st.text_input("💊 Medicine Name", placeholder="e.g., Aspirin, Metformin")
                        dosage = st.text_input("📏 Dosage", placeholder="e.g., 100mg, 1 tablet")
                        frequency = st.selectbox("🔄 Frequency", 
                                                ["Once daily", "Twice daily", "Three times daily", 
                                                 "Every 6 hours", "Every 8 hours", "As needed"])
                    
                    with col2:
                        start_date = st.date_input("📅 Start Date", value=datetime.now().date())
                        end_date = st.date_input("📅 End Date", value=datetime.now().date() + timedelta(days=30))
                        
                        # Time selection based on frequency
                        st.markdown("⏰ **Select Times:**")
                        time_slots = []
                        
                        if "Once" in frequency:
                            time_slots.append(st.time_input("Time", value=datetime.strptime("09:00", "%H:%M").time()).strftime("%H:%M"))
                        elif "Twice" in frequency:
                            col_t1, col_t2 = st.columns(2)
                            with col_t1:
                                time_slots.append(st.time_input("Morning", value=datetime.strptime("09:00", "%H:%M").time()).strftime("%H:%M"))
                            with col_t2:
                                time_slots.append(st.time_input("Evening", value=datetime.strptime("21:00", "%H:%M").time()).strftime("%H:%M"))
                        elif "Three" in frequency:
                            col_t1, col_t2, col_t3 = st.columns(3)
                            with col_t1:
                                time_slots.append(st.time_input("Morning", value=datetime.strptime("09:00", "%H:%M").time()).strftime("%H:%M"))
                            with col_t2:
                                time_slots.append(st.time_input("Afternoon", value=datetime.strptime("15:00", "%H:%M").time()).strftime("%H:%M"))
                            with col_t3:
                                time_slots.append(st.time_input("Evening", value=datetime.strptime("21:00", "%H:%M").time()).strftime("%H:%M"))
                    
                    special_instructions = st.text_area("📝 Special Instructions", 
                                                      placeholder="e.g., Take with food, avoid alcohol")
                    
                    submit_reminder = st.form_submit_button("💾 Save Medication Reminder", use_container_width=True)
                    
                    if submit_reminder and medicine_name and dosage and time_slots:
                        add_medicine_reminder(
                            st.session_state.user['id'],
                            medicine_name,
                            dosage,
                            frequency,
                            time_slots,
                            start_date.strftime('%Y-%m-%d'),
                            end_date.strftime('%Y-%m-%d')
                        )
                        st.success("✅ Medication reminder created successfully!")
                        st.balloons()
                        st.rerun()
            
            with tab3:
                st.markdown("### 🔔 Health Notifications & Reminders")
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown("""
                    #### 💧 Water Intake Reminders
                    Stay hydrated throughout the day with personalized reminders.
                    """)
                    
                    water_frequency = st.selectbox("Reminder frequency:", 
                                                 ["Every 2 hours", "Every 3 hours", "Every 4 hours"])
                    
                    if st.button("💧 Enable Water Reminders", use_container_width=True):
                        hours = int(water_frequency.split()[1])
                        create_water_reminder(st.session_state.user['id'], hours)
                        st.success(f"💧 Water reminders set for every {hours} hours!")
                
                with col2:
                    st.markdown("""
                    #### 🏃‍♂️ Exercise Reminders  
                    Get motivated to stay active with regular exercise prompts.
                    """)
                    
                    exercise_time = st.time_input("Preferred exercise time:", value=datetime.strptime("07:00", "%H:%M").time())
                    
                    if st.button("🏃‍♂️ Enable Exercise Reminders", use_container_width=True):
                        st.success("🏃‍♂️ Exercise reminders activated!")
                
                # Recent notifications
                if st.session_state.notifications:
                    st.markdown("---")
                    st.markdown("### 📬 Recent Notifications")
                    
                    for notification in st.session_state.notifications[-10:]:
                        notification_type = "💧" if notification['type'] == 'water' else "💊" if notification['type'] == 'medicine' else "🔔"
                        
                        st.markdown(f"""
                        <div class="notification-card">
                            <span style="font-size: 1.2rem;">{notification_type}</span>
                            <strong>{notification['message']}</strong>
                            <br><small style="opacity: 0.7;">⏰ {notification['time']}</small>
                        </div>
                        """, unsafe_allow_html=True)
        
        elif st.session_state.page == 'diagnosis':
            st.markdown("## 🩺 AI-Powered Symptom Analysis")
            st.markdown("Describe your symptoms for a comprehensive health assessment tailored to your profile.")
            
            # User type specific instructions
            user_type = st.session_state.user.get('user_type', 'patient')
            
            if user_type == 'medical_student':
                st.info("📚 **Student Mode**: This analysis will include educational context and differential diagnosis considerations.")
            elif user_type == 'healthcare_professional':
                st.info("👩‍⚕️ **Professional Mode**: Assessment includes clinical guidelines and professional insights.")
            
            with st.form("diagnosis_form"):
                col1, col2 = st.columns([3, 1])
                
                with col1:
                    symptoms = st.text_area(
                        "Describe symptoms in detail:",
                        placeholder="e.g., I've been experiencing chest pain for 2 hours, accompanied by shortness of breath and sweating. The pain is crushing and radiates to my left arm...",
                        height=150
                    )
                
                with col2:
                    st.markdown("### 🎯 Quick Symptom Checker")
                    common_symptoms = st.multiselect(
                        "Select common symptoms:",
                        ["Fever", "Headache", "Nausea", "Fatigue", "Cough", 
                         "Chest Pain", "Abdominal Pain", "Dizziness", "Rash"]
                    )
                    
                    severity_self_assessment = st.slider("Pain level (1-10):", 1, 10, 5)
                
                analyze_button = st.form_submit_button("🔍 Analyze Symptoms", use_container_width=True)
            
            # Initialize session state for results
            if 'diagnosis_result' not in st.session_state:
                st.session_state.diagnosis_result = None
            if 'recommendations_result' not in st.session_state:
                st.session_state.recommendations_result = None
            if 'severity_result' not in st.session_state:
                st.session_state.severity_result = None
            if 'symptoms_result' not in st.session_state:
                st.session_state.symptoms_result = None
            if 'professional_note_result' not in st.session_state:
                st.session_state.professional_note_result = None
            
            if analyze_button and (symptoms or common_symptoms):
                # Combine text and selected symptoms
                full_symptoms = symptoms
                if common_symptoms:
                    full_symptoms += f"\nAdditional symptoms: {', '.join(common_symptoms)}"
                if severity_self_assessment >= 7:
                    full_symptoms += f"\nSevere pain level: {severity_self_assessment}/10"
                
                diagnosis, severity, recommendations, professional_note = analyze_symptoms(full_symptoms, user_type)
                
                # Store results in session state
                st.session_state.diagnosis_result = diagnosis
                st.session_state.recommendations_result = recommendations
                st.session_state.severity_result = severity
                st.session_state.symptoms_result = full_symptoms
                st.session_state.professional_note_result = professional_note
                
                # Save consultation
                save_consultation(
                    st.session_state.user['id'],
                    full_symptoms,
                    diagnosis,
                    ', '.join(recommendations),
                    severity
                )
            
            # Display results if available
            if st.session_state.diagnosis_result and st.session_state.symptoms_result:
                diagnosis = st.session_state.diagnosis_result
                recommendations = st.session_state.recommendations_result
                severity = st.session_state.severity_result
                symptoms = st.session_state.symptoms_result
                professional_note = st.session_state.professional_note_result
                
                st.markdown("---")
                
                # Display diagnosis with appropriate styling
                if severity == "CRITICAL":
                    st.markdown(f"""
                    <div class="emergency-alert">
                        <h2>🚨 {diagnosis}</h2>
                        <p style="font-size: 1.1rem; margin-top: 1rem;">
                            This appears to be a medical emergency. Immediate action is required.
                        </p>
                    </div>
                    """, unsafe_allow_html=True)
                elif severity == "High":
                    st.error(f"⚠️ **{diagnosis}**")
                elif severity == "Medium":
                    st.warning(f"🟡 **{diagnosis}**")
                else:
                    st.success(f"ℹ️ **{diagnosis}**")
                
                # Professional context
                if professional_note:
                    st.info(professional_note)
                
                # Recommendations
                st.markdown("### 📝 Personalized Recommendations")
                
                for i, rec in enumerate(recommendations, 1):
                    if severity == "CRITICAL":
                        st.error(f"**{i}.** {rec}")
                    elif severity == "High":
                        st.warning(f"**{i}.** {rec}")
                    else:
                        st.info(f"**{i}.** {rec}")
                
                # Additional resources based on user type
                if user_type == 'medical_student':
                    with st.expander("📚 Educational Resources & Learning Points"):
                        st.markdown("""
                        **Learning Objectives:**
                        - Practice systematic symptom assessment
                        - Consider differential diagnosis approach
                        - Review pathophysiology of identified conditions
                        - Study evidence-based treatment protocols
                        
                        **Suggested Reading:**
                        - Harrison's Principles of Internal Medicine
                        - Current Medical Diagnosis & Treatment
                        - Clinical examination techniques
                        """)
                
                elif user_type == 'healthcare_professional':
                    with st.expander("👩‍⚕️ Clinical Decision Support"):
                        st.markdown("""
                        **Clinical Considerations:**
                        - Review patient history and comorbidities
                        - Consider diagnostic workup and imaging
                        - Evaluate need for specialist consultation
                        - Document findings and follow-up plan
                        
                        **Guidelines & Protocols:**
                        - Latest clinical practice guidelines
                        - Institutional protocols
                        - Quality measures and indicators
                        """)
                
                # Download options
                st.markdown("---")
                col1, col2, col3 = st.columns(3)
                
                consultation_data = {
                    'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'symptoms': symptoms,
                    'diagnosis': diagnosis,
                    'severity': severity,
                    'recommendations': recommendations,
                    'user_type': user_type,
                    'professional_note': professional_note
                }
                
                with col1:
                    pdf_buffer = generate_pdf_report(consultation_data, st.session_state.user)
                    st.download_button(
                        label="📄 Download PDF Report",
                        data=pdf_buffer.getvalue(),
                        file_name=f"medical_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                        mime="application/pdf",
                        use_container_width=True
                    )
                
                with col2:
                    json_data = json.dumps(consultation_data, indent=2)
                    st.download_button(
                        label="💾 Download JSON Data",
                        data=json_data,
                        file_name=f"medical_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                        mime="application/json",
                        use_container_width=True
                    )
                
                with col3:
                    if st.button("🔄 New Analysis", use_container_width=True):
                        # Clear results
                        st.session_state.diagnosis_result = None
                        st.session_state.recommendations_result = None
                        st.session_state.severity_result = None
                        st.session_state.symptoms_result = None
                        st.session_state.professional_note_result = None
                        st.rerun()
        
        elif st.session_state.page == 'chatbot':
            st.markdown("## 💬 AI Medical Assistant")
            st.markdown("Ask questions and get personalized medical information based on your profile.")
            
            # Voice controls section
            col1, col2 = st.columns([3, 1])
            
            with col2:
                st.markdown("""
                <div class="voice-controls">
                    <h4 style="color: white; margin-bottom: 1rem;">🎤 Voice Assistant</h4>
                </div>
                """, unsafe_allow_html=True)
                
                voice_enabled = st.checkbox("🎤 Enable Voice Input", value=st.session_state.voice_enabled)
                st.session_state.voice_enabled = voice_enabled
                
                if voice_enabled:
                    st.info("🎤 Voice input would be enabled here using Web Speech API in a full deployment")
                    if st.button("🎙️ Start Recording", use_container_width=True):
                        st.success("Recording... (simulated)")
                
                text_to_speech = st.checkbox("🔊 Text-to-Speech Response")
                if text_to_speech:
                    st.info("🔊 AI responses would be read aloud using browser's speech synthesis")
            
            with col1:
                # Chat history
                if 'chat_history' not in st.session_state:
                    st.session_state.chat_history = []
                
                # Display chat history
                chat_container = st.container()
                
                with chat_container:
                    for i, (question, answer) in enumerate(st.session_state.chat_history):
                        # User message
                        st.markdown(f"""
                        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                                    color: white; padding: 1rem; border-radius: 15px 15px 5px 15px; 
                                    margin: 0.5rem 0; margin-left: 20%;">
                            <strong>You:</strong> {question}
                        </div>
                        """, unsafe_allow_html=True)
                        
                        # AI response
                        st.markdown(f"""
                        <div style="background: white; color: #2d3436; padding: 1rem; 
                                    border-radius: 15px 15px 15px 5px; margin: 0.5rem 0; 
                                    margin-right: 20%; box-shadow: 0 2px 8px rgba(0,0,0,0.1); 
                                    border-left: 4px solid #00b894;">
                            <strong>🩺 MediBot:</strong><br>{answer}
                        </div>
                        """, unsafe_allow_html=True)
                
                # New question input
                with st.form("chat_form", clear_on_submit=True):
                    col_input, col_submit = st.columns([4, 1])
                    
                    with col_input:
                        question = st.text_input(
                            "Ask your health question:",
                            placeholder="e.g., What are the symptoms of diabetes? How do I manage high blood pressure?",
                            label_visibility="collapsed"
                        )
                    
                    with col_submit:
                        ask_button = st.form_submit_button("💬 Ask", use_container_width=True)
                    
                    # Quick question buttons
                    st.markdown("**Quick Questions:**")
                    quick_questions = [
                        "What should I do for a fever?",
                        "How do I treat a headache?", 
                        "What are signs of dehydration?",
                        "When should I see a doctor?"
                    ]
                    
                    cols = st.columns(len(quick_questions))
                    for i, quick_q in enumerate(quick_questions):
                        with cols[i]:
                            if st.form_submit_button(quick_q.split('?')[0] + '?', use_container_width=True):
                                question = quick_q
                                ask_button = True
                    
                    if ask_button and question:
                        with st.spinner("🤔 Thinking..."):
                            answer = medical_chatbot_response(question, st.session_state.user.get('user_type', 'patient'))
                            st.session_state.chat_history.append((question, answer))
                            
                            # Simulate text-to-speech
                            if text_to_speech:
                                st.success("🔊 Response would be read aloud")
                            
                            st.rerun()
                
                # Chat controls
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("🗑️ Clear Chat History", use_container_width=True):
                        st.session_state.chat_history = []
                        st.rerun()
                
                with col2:
                    if st.session_state.chat_history:
                        chat_export = json.dumps({
                            'user': st.session_state.user['username'],
                            'chat_history': st.session_state.chat_history,
                            'timestamp': datetime.now().isoformat()
                        }, indent=2)
                        
                        st.download_button(
                            label="📥 Export Chat",
                            data=chat_export,
                            file_name=f"chat_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                            mime="application/json",
                            use_container_width=True
                        )
        
        elif st.session_state.page == 'hospitals':
            st.markdown("## 🏥 Hospital & Healthcare Facility Finder")
            st.markdown("Locate nearby hospitals, urgent care centers, and specialized medical facilities.")
            
            col1, col2, col3 = st.columns([2, 2, 1])
            
            with col1:
                city = st.text_input("🏙️ City", value=st.session_state.get('hospital_city', 'New York'), key='hospital_city')
            
            with col2:
                state = st.text_input("🗺️ State", value=st.session_state.get('hospital_state', 'NY'), key='hospital_state')
            
            with col3:
                emergency_only = st.checkbox("🚨 Emergency Only", value=False)
            
            # Search filters
            with st.expander("🔍 Advanced Search Filters"):
                col1, col2 = st.columns(2)
                
                with col1:
                    specialty_filter = st.multiselect(
                        "Specialties:",
                        ["Emergency Medicine", "Cardiology", "Neurology", "Pediatrics", 
                         "Surgery", "Orthopedics", "Oncology", "Mental Health"]
                    )
                    
                    min_rating = st.slider("Minimum Rating:", 1.0, 5.0, 3.0, 0.1)
                
                with col2:
                    max_distance = st.selectbox("Maximum Distance:", 
                                              ["5 miles", "10 miles", "25 miles", "50 miles", "Any"])
                    
                    sort_by = st.selectbox("Sort by:", 
                                         ["Distance", "Rating", "Emergency Services", "Specialties"])
            
            # --- Only create/update the map when the button is pressed ---
            if st.button("🔍 Find Healthcare Facilities", use_container_width=True, type="primary"):
                with st.spinner("🔍 Searching for healthcare facilities..."):
                    hospitals = find_nearby_hospitals(city, state)
                    # Apply filters
                    if emergency_only:
                        hospitals = [h for h in hospitals if h['emergency']]
                    if specialty_filter:
                        hospitals = [h for h in hospitals if any(spec in h.get('specialties', []) for spec in specialty_filter)]
                    # Filter by rating
                    hospitals = [h for h in hospitals if h['rating'] >= min_rating]
                    # Sort results
                    if sort_by == "Rating":
                        hospitals.sort(key=lambda x: x['rating'], reverse=True)
                    elif sort_by == "Emergency Services":
                        hospitals.sort(key=lambda x: x['emergency'], reverse=True)
                    # Use a stable city center for the map to prevent blinking
                    city_centers = {
                        'Chennai': [13.0827, 80.2707],
                        'Coimbatore': [11.0168, 76.9558],
                        'Madurai': [9.9252, 78.1198],
                        'Tiruchirappalli': [10.7905, 78.7047],
                        'Salem': [11.6643, 78.1460],
                        # Add more Tamil Nadu cities as needed
                    }
                    city_key = city.strip().title()
                    default_center = city_centers.get(city_key, [13.0827, 80.2707])  # Default: Chennai center
                    if hospitals:
                        avg_lat = sum(h['lat'] for h in hospitals) / len(hospitals)
                        avg_lng = sum(h['lng'] for h in hospitals) / len(hospitals)
                        map_center = [avg_lat, avg_lng]
                    else:
                        map_center = default_center
                    import folium
                    m = folium.Map(location=map_center, zoom_start=14, tiles=None)
                    folium.TileLayer(
                        tiles='https://mt1.google.com/vt/lyrs=r&x={x}&y={y}&z={z}',
                        attr='Google',
                        name='Google Maps',
                        overlay=False,
                                               control=True
                    ).add_to(m)
                    for hospital in hospitals:
                        popup_text = f"""
                        <div style='width: 200px;'>
                        <b>{hospital['name']}</b><br>
                        <i class='fa fa-map-marker'></i> {hospital['address']}<br>
                        <i class='fa fa-phone'></i> {hospital['phone']}<br>
                        <i class='fa fa-star'></i> Rating: {hospital['rating']}/5<br>
                        <i class='fa fa-ambulance'></i> Emergency: {'Yes' if hospital['emergency'] else 'No'}<br>
                        <a href='https://www.google.com/maps/search/?api=1&query={hospital['lat']},{hospital['lng']}' target='_blank'>Open in Google Maps</a>
                        </div>
                        """
                        folium.Marker(
                            [hospital['lat'], hospital['lng']],
                            popup=folium.Popup(popup_text, max_width=300),
                            tooltip=hospital['name'],
                            icon=folium.Icon(
                                color='red' if hospital['emergency'] else 'blue',
                                icon='plus',
                                prefix='fa'
                            )
                        ).add_to(m)
                    st.session_state.hospital_map = m
                    st.session_state.hospital_results = hospitals
                    if not hospitals:
                        st.warning("No facilities found matching your criteria. Try adjusting your filters.")

            # --- Always display the map and results from session state (never recreate here) ---
            if hasattr(st.session_state, 'hospital_map'):
                st.markdown('<div class="map-container">', unsafe_allow_html=True)
                st_folium(st.session_state.hospital_map, width=700, height=400)
                st.markdown('</div>', unsafe_allow_html=True)
                if hasattr(st.session_state, 'hospital_results') and st.session_state.hospital_results:
                    st.markdown(f"### 🏥 Found {len(st.session_state.hospital_results)} Healthcare Facilities")
                    st.markdown("---")
                    for hospital in st.session_state.hospital_results:
                        emergency_badge = "🚨 Emergency" if hospital['emergency'] else "🏥 General"
                        rating_stars = "⭐" * int(hospital['rating']) + "☆" * (5 - int(hospital['rating']))
                        with st.expander(f"{emergency_badge} | {hospital['name']} | {rating_stars} ({hospital['rating']})"):
                            col1, col2 = st.columns([2, 1])
                            with col1:
                                st.markdown(f"**📍 Address:** {hospital['address']}")
                                st.markdown(f"**📞 Phone:** {hospital['phone']}")
                                st.markdown(f"**🏥 Bed Capacity:** {hospital.get('beds', 'N/A')}")
                                st.markdown(f"**🚨 Emergency Services:** {'Available 24/7' if hospital['emergency'] else 'Not Available'}")
                                if hospital.get('specialties'):
                                    st.markdown(f"**🔬 Specialties:** {', '.join(hospital['specialties'])}")
                            with col2:
                                st.markdown(f"**⭐ Rating:** {hospital['rating']}/5.0")
                                if st.button(f"📞 Call {hospital['name']}", key=f"call_{hospital['name']}"):
                                    st.success(f"Calling {hospital['phone']}... (simulated)")
                                if st.button(f"🗺️ Get Directions", key=f"directions_{hospital['name']}"):
                                    st.info(f"Opening directions to {hospital['address']}... (would integrate with maps)")
                                if hospital['emergency']:
                                    if st.button(f"🚨 Emergency Contact", key=f"emergency_{hospital['name']}", type="primary"):
                                        st.error(f"🚨 Contacting {hospital['name']} emergency department...")
        
        elif st.session_state.page == 'notifications':
            st.markdown("## 🔔 Notification Center")
            
            tab1, tab2 = st.tabs(["📬 All Notifications", "⚙️ Settings"])
            
            with tab1:
                if st.session_state.notifications:
                    st.markdown(f"### 📬 Your Notifications ({len(st.session_state.notifications)})")
                    
                    # Group notifications by type
                    notification_types = {}
                    for notif in st.session_state.notifications:
                        notif_type = notif['type']
                        if notif_type not in notification_types:
                            notification_types[notif_type] = []
                        notification_types[notif_type].append(notif)
                    
                    for notif_type, notifications in notification_types.items():
                        type_icon = "💧" if notif_type == 'water' else "💊" if notif_type == 'medicine' else "🔔"
                        type_name = notif_type.replace('_', ' ').title()
                        
                        with st.expander(f"{type_icon} {type_name} ({len(notifications)})"):
                            for notif in notifications[-10:]:  # Show last 10 of each type
                                st.markdown(f"""
                                <div style="background: white; padding: 0.75rem; border-radius: 8px; 
                                           margin: 0.25rem 0; border-left: 3px solid #667eea;">
                                    <strong>{notif['message']}</strong>
                                    <br><small style="color: #636e72;">⏰ {notif['time']}</small>
                                </div>
                                """, unsafe_allow_html=True)
                    
                    if st.button("🗑️ Clear All Notifications", type="secondary"):
                        st.session_state.notifications = []
                        st.success("All notifications cleared!")
                        st.rerun()
                
                else:
                    st.info("📭 No notifications yet. Your reminders and alerts will appear here.")
            
            with tab2:
                st.markdown("### ⚙️ Notification Preferences")
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown("#### 💊 Medicine Reminders")
                    medicine_notifications = st.radio(
                        "Medicine reminder notifications:",
                        ["All reminders", "Critical only", "Disabled"],
                        index=0
                    )
                    
                    snooze_duration = st.selectbox(
                        "Snooze duration:",
                        ["5 minutes", "10 minutes", "15 minutes", "30 minutes"]
                    )
                    
                    st.markdown("#### 💧 Health Reminders")
                    water_reminders = st.checkbox("Water intake reminders", value=True)
                    exercise_reminders = st.checkbox("Exercise reminders", value=True)
                    sleep_reminders = st.checkbox("Sleep schedule reminders", value=False)
                
                with col2:
                    st.markdown("#### 🔊 Alert Settings")
                    sound_alerts = st.checkbox("Sound alerts", value=True)
                    push_notifications = st.checkbox("Push notifications", value=True)
                    email_notifications = st.checkbox("Email notifications", value=False)
                    
                    st.markdown("#### ⏰ Quiet Hours")
                    quiet_start = st.time_input("Quiet hours start:", value=datetime.strptime("22:00", "%H:%M").time())
                    quiet_end = st.time_input("Quiet hours end:", value=datetime.strptime("07:00", "%H:%M").time())
                
                if st.button("💾 Save Notification Settings", use_container_width=True, type="primary"):
                    st.success("✅ Notification preferences saved successfully!")
        
        elif st.session_state.page == 'reports':
            st.markdown("## 📊 Health Reports & Analytics")
            st.markdown("Comprehensive view of your health data and consultation history.")
            
            consultations = get_user_consultations(st.session_state.user['id'])
            
            if consultations:
                # Enhanced analytics dashboard
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    st.metric("📋 Total Consultations", len(consultations))
                
                with col2:
                    critical_count = len([c for c in consultations if c[5] == 'CRITICAL'])
                    st.metric("⚠️ Critical Cases", critical_count)
                
                with col3:
                    recent_count = len([c for c in consultations if 
                                      (datetime.now() - datetime.strptime(c[6], '%Y-%m-%d %H:%M:%S')).days <= 30])
                    st.metric("📅 Last 30 Days", recent_count)
                
                with col4:
                    avg_gap = "N/A"
                    if len(consultations) > 1:
                        dates = [datetime.strptime(c[6], '%Y-%m-%d %H:%M:%S') for c in consultations]
                        dates.sort()
                        gaps = [(dates[i] - dates[i-1]).days for i in range(1, len(dates))]
                        avg_gap = f"{sum(gaps) // len(gaps)} days"
                    st.metric("📊 Avg. Gap", avg_gap)
                
                # Filters and analytics
                tab1, tab2, tab3 = st.tabs(["📋 Consultation History", "📈 Health Analytics", "💾 Export Data"])
                
                with tab1:
                    # Enhanced filtering
                    col1, col2, col3 = st.columns(3)
                    
                    with col1:
                        severity_filter = st.selectbox(
                            "Filter by Severity:",
                            options=['All', 'CRITICAL', 'High', 'Medium', 'Low']
                        )
                    
                    with col2:
                        date_range = st.selectbox(
                            "Date Range:",
                            options=['All Time', 'Last 7 Days', 'Last 30 Days', 'Last 90 Days', 'Last Year']
                        )
                    
                    with col3:
                        sort_option = st.selectbox(
                            "Sort by:",
                            options=['Newest First', 'Oldest First', 'Severity (High to Low)', 'Severity (Low to High)']
                        )
                    
                    # Apply filters
                    filtered_consultations = consultations.copy()
                    
                    if severity_filter != 'All':
                        filtered_consultations = [c for c in filtered_consultations if c[5] == severity_filter]
                    
                    if date_range != 'All Time':
                        days_map = {
                            'Last 7 Days': 7, 'Last 30 Days': 30, 
                            'Last 90 Days': 90, 'Last Year': 365
                        }
                        days = days_map[date_range]
                        filtered_consultations = [
                            c for c in filtered_consultations 
                            if (datetime.now() - datetime.strptime(c[6], '%Y-%m-%d %H:%M:%S')).days <= days
                        ]
                    
                    # Apply sorting
                    if sort_option == 'Oldest First':
                        filtered_consultations.reverse()
                    elif 'Severity' in sort_option:
                        severity_order = {'CRITICAL': 4, 'High': 3, 'Medium': 2, 'Low': 1}
                        reverse_sort = 'High to Low' in sort_option
                        filtered_consultations.sort(key=lambda x: severity_order.get(x[5], 0), reverse=reverse_sort)
                    
                    st.markdown(f"### 📋 Showing {len(filtered_consultations)} consultations")
                    
                    # Display consultations with enhanced cards
                    for i, consultation in enumerate(filtered_consultations):
                        severity_colors = {
                            'CRITICAL': '#e74c3c',
                            'High': '#f39c12', 
                            'Medium': '#f1c40f',
                            'Low': '#27ae60'
                        }
                        
                        severity_icons = {
                            'CRITICAL': '🚨',
                            'High': '⚠️',
                            'Medium': '🟡',
                            'Low': '✅'
                        }
                        
                        color = severity_colors.get(consultation[5], '#95a5a6')
                        icon = severity_icons.get(consultation[5], '📋')
                        
                        with st.expander(f"{icon} {consultation[6][:16]} - {consultation[5]} Severity"):
                            st.markdown(f"""
                            <div style="border-left: 4px solid {color}; padding-left: 1rem; margin-bottom: 1rem;">
                                <h4 style="color: {color}; margin-bottom: 0.5rem;">
                                    {icon} {consultation[5]} Priority Case
                                </h4>
                                <p style="margin-bottom: 0.5rem;"><strong>Date:</strong> {consultation[6]}</p>
                            </div>
                            """, unsafe_allow_html=True)
                            
                            col1, col2 = st.columns([3, 1])
                            
                            with col1:
                                st.markdown("**🩺 Reported Symptoms:**")
                                st.write(consultation[2])
                                
                                st.markdown("**🔍 AI Assessment:**")
                                st.write(consultation[3])
                                
                                st.markdown("**💡 Recommendations:**")
                                recommendations = consultation[4].split(', ')
                                for rec in recommendations:
                                    st.write(f"• {rec}")
                            
                            with col2:
                                st.markdown(f"**⚠️ Severity:** {consultation[5]}")
                                st.markdown(f"**📅 Date:** {consultation[6][:10]}")
                                st.markdown(f"**⏰ Time:** {consultation[6][11:16]}")
                                
                                # Individual report download
                                consultation_data = {
                                    'date': consultation[6],
                                    'symptoms': consultation[2],
                                    'diagnosis': consultation[3],
                                    'severity': consultation[5],
                                    'recommendations': consultation[4].split(', ')
                                }
                                
                                pdf_buffer = generate_pdf_report(consultation_data, st.session_state.user)
                                
                                st.download_button(
                                    label="📄 PDF",
                                    data=pdf_buffer.getvalue(),
                                    file_name=f"report_{consultation[6][:10]}_{i}.pdf",
                                    mime="application/pdf",
                                    key=f"pdf_download_{i}",
                                    use_container_width=True
                                )
                
                with tab2:
                    st.markdown("### 📈 Health Analytics & Insights")
                    
                    if len(consultations) >= 2:
                        # Health trends analysis
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            st.markdown("#### 📊 Severity Distribution")
                            severity_counts = {}
                            for consultation in consultations:
                                severity = consultation[5]
                                severity_counts[severity] = severity_counts.get(severity, 0) + 1
                            
                            severity_df = pd.DataFrame(
                                list(severity_counts.items()), 
                                columns=['Severity', 'Count']
                            )
                            st.bar_chart(severity_df.set_index('Severity'))
                        
                        with col2:
                            st.markdown("#### 📅 Monthly Activity")
                            monthly_counts = {}
                            for consultation in consultations:
                                month = consultation[6][:7]  # YYYY-MM
                                monthly_counts[month] = monthly_counts.get(month, 0) + 1
                            
                            if len(monthly_counts) > 1:
                                monthly_df = pd.DataFrame(
                                    list(monthly_counts.items()), 
                                    columns=['Month', 'Consultations']
                                )
                                st.line_chart(monthly_df.set_index('Month'))
                            else:
                                st.info("Need more data points for trend analysis")
                        
                        # Health insights
                        st.markdown("#### 🔍 AI Health Insights")
                        
                        # Calculate patterns
                        recent_severity = [c[5] for c in consultations[:5]]  # Last 5 consultations
                        critical_trend = recent_severity.count('CRITICAL')
                        high_trend = recent_severity.count('High')
                        
                        if critical_trend > 0:
                            st.error(f"⚠️ You have {critical_trend} critical case(s) in your recent history. Consider following up with healthcare providers.")
                        elif high_trend >= 2:
                            st.warning(f"🟡 You have {high_trend} high-priority cases recently. Monitor symptoms and seek medical advice.")
                        else:
                            st.success("✅ Your recent health consultations show manageable concerns.")
                        
                        # Symptom analysis
                        all_symptoms = ' '.join([c[2] for c in consultations]).lower()
                        common_keywords = ['headache', 'fever', 'pain', 'nausea', 'fatigue', 'cough']
                        symptom_frequency = {keyword: all_symptoms.count(keyword) for keyword in common_keywords if all_symptoms.count(keyword) > 0}
                        
                        if symptom_frequency:
                            st.markdown("#### 🎯 Most Reported Symptoms")
                            for symptom, count in sorted(symptom_frequency.items(), key=lambda x: x[1], reverse=True)[:5]:
                                st.write(f"• **{symptom.title()}:** {count} times")
                    
                    else:
                        st.info("📊 More consultation data needed for detailed analytics. Continue using the symptom checker to build your health profile.")
                
                with tab3:
                    st.markdown("### 💾 Export & Backup Your Health Data")
                    
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        st.markdown("#### 📄 Individual Reports")
                        
                        export_format = st.selectbox(
                            "Choose format:",
                            ["PDF Report", "JSON Data", "CSV Summary"]
                        )
                        
                        selected_consultations = st.multiselect(
                            "Select consultations to export:",
                            options=[(i, f"{c[6][:16]} - {c[5]}") for i, c in enumerate(consultations)],
                            format_func=lambda x: x[1],
                            default=[(i, f"{c[6][:16]} - {c[5]}") for i, c in enumerate(consultations[:3])]
                        )
                        
                        if st.button("📥 Export Selected", use_container_width=True) and selected_consultations:
                            if export_format == "PDF Report":
                                st.info("Individual PDF exports available in the consultation cards above")
                            elif export_format == "JSON Data":
                                selected_data = []
                                for idx, _ in selected_consultations:
                                    consultation = consultations[idx]
                                    selected_data.append({
                                        'date': consultation[6],
                                        'symptoms': consultation[2],
                                        'diagnosis': consultation[3],
                                        'severity': consultation[5],
                                        'recommendations': consultation[4].split(', ')
                                    })
                                
                                export_json = json.dumps({
                                    'user': st.session_state.user['username'],
                                    'export_date': datetime.now().isoformat(),
                                    'consultations': selected_data
                                }, indent=2)
                                
                                st.download_button(
                                    label="📥 Download JSON Export",
                                    data=export_json,
                                    file_name=f"health_data_export_{datetime.now().strftime('%Y%m%d')}.json",
                                    mime="application/json",
                                    use_container_width=True
                                )
                    
                    with col2:
                        st.markdown("#### 📊 Complete Health Summary")
                        
                        if st.button("📋 Generate Comprehensive Report", use_container_width=True):
                            # Create comprehensive health summary
                            summary_data = {
                                'user_profile': {
                                    'username': st.session_state.user['username'],
                                    'user_type': st.session_state.user.get('user_type', 'patient'),
                                    'total_consultations': len(consultations),
                                    'date_range': f"{consultations[-1][6][:10]} to {consultations[0][6][:10]}" if consultations else "N/A"
                                },
                                'health_statistics': {
                                    'critical_cases': len([c for c in consultations if c[5] == 'CRITICAL']),
                                    'high_priority': len([c for c in consultations if c[5] == 'High']),
                                    'medium_priority': len([c for c in consultations if c[5] == 'Medium']),
                                    'low_priority': len([c for c in consultations if c[5] == 'Low'])
                                },
                                'recent_activity': [
                                    {
                                        'date': c[6],
                                        'severity': c[5],
                                        'symptoms_summary': c[2][:100] + '...' if len(c[2]) > 100 else c[2]
                                    } for c in consultations[:10]
                                ]
                            }
                            
                            summary_json = json.dumps(summary_data, indent=2)
                            
                            st.download_button(
                                label="📥 Download Complete Summary",
                                data=summary_json,
                                file_name=f"complete_health_summary_{datetime.now().strftime('%Y%m%d')}.json",
                                mime="application/json",
                                use_container_width=True
                            )
                        
                        st.markdown("#### 🔄 Data Backup")
                        st.info("💡 **Tip:** Regularly backup your health data for your records and to share with healthcare providers.")
                        
                        if st.button("☁️ Backup All Data", use_container_width=True):
                            # Complete data backup
                            backup_data = {
                                'backup_info': {
                                    'created_date': datetime.now().isoformat(),
                                    'user_id': st.session_state.user['id'],
                                    'username': st.session_state.user['username'],
                                    'backup_version': '1.0'
                                },
                                'consultations': [
                                    {
                                        'id': c[0],
                                        'symptoms': c[2],
                                        'diagnosis': c[3],
                                        'recommendations': c[4],
                                        'severity': c[5],
                                        'date': c[6]
                                    } for c in consultations
                                ],
                                'reminders': [
                                    {
                                        'medicine_name': r[2],
                                        'dosage': r[3],
                                        'frequency': r[4],
                                        'time_slots': json.loads(r[5]) if r[5] else [],
                                        'start_date': r[6],
                                        'end_date': r[7],
                                        'active': r[8]
                                    } for r in get_user_reminders(st.session_state.user['id'])
                                ]
                            }
                            
                            backup_json = json.dumps(backup_data, indent=2)
                            
                            st.download_button(
                                label="📥 Download Complete Backup",
                                data=backup_json,
                                file_name=f"aegis_health_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                                mime="application/json",
                                use_container_width=True
                            )
            
            else:
                st.info("📋 No consultation records found. Start by using our Symptom Diagnosis tool to build your health profile!")
                
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    if st.button("🩺 Start First Diagnosis", use_container_width=True, type="primary"):
                        st.session_state.page = 'diagnosis'
                        st.rerun()
                
                with col2:
                    if st.button("💬 Ask Health Questions", use_container_width=True):
                        st.session_state.page = 'chatbot'
                        st.rerun()
                
                with col3:
                    if st.button("🏥 Find Nearby Care", use_container_width=True):
                        st.session_state.page = 'hospitals'
                        st.rerun()

        # Footer with additional information
        st.markdown("---")
        st.markdown("""
        <div style="text-align: center; padding: 2rem; background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%); border-radius: 15px; margin-top: 2rem;">
            <h3 style="color: #495057; margin-bottom: 1rem;">🩺 AEGIS HEALTH</h3>
            <p style="color: #6c757d; margin-bottom: 1rem;">Empowering better health decisions through AI-driven insights</p>
            <div style="display: flex; justify-content: center; gap: 2rem; flex-wrap: wrap;">
                <div style="text-align: center;">
                    <h4 style="color: #007bff; margin-bottom: 0.5rem;">For Patients</h4>
                    <p style="color: #6c757d; font-size: 0.9rem;">Personalized health guidance<br>Symptom assessment<br>Medication reminders</p>
                </div>
                <div style="text-align: center;">
                    <h4 style="color: #28a745; margin-bottom: 0.5rem;">For Students</h4>
                    <p style="color: #6c757d; font-size: 0.9rem;">Educational resources<br>Case study analysis<br>Learning objectives</p>
                </div>
                <div style="text-align: center;">
                    <h4 style="color: #dc3545; margin-bottom: 0.5rem;">For Professionals</h4>
                    <p style="color: #6c757d; font-size: 0.9rem;">Clinical decision support<br>Latest guidelines<br>Professional insights</p>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        # Disclaimer
        st.markdown("""
        <div style="background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 10px; padding: 1rem; margin-top: 1rem;">
            <h4 style="color: #856404; margin-bottom: 0.5rem;">⚠️ Important Medical Disclaimer</h4>
            <p style="color: #856404; font-size: 0.9rem; margin: 0; line-height: 1.5;">
                AEGIS HEALTH provides AI-generated health information for educational and informational purposes only. 
                This system does not replace professional medical advice, diagnosis, or treatment. Always consult 
                qualified healthcare providers for medical decisions. In medical emergencies, contact emergency services immediately.
                The accuracy of AI-generated content may vary and should not be solely relied upon for medical decisions.
            </p>
        </div>
        """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
    %