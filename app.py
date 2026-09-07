from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
import os
import json
import re

from PyPDF2 import PdfReader
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename


# ============================================================
# OPTIONAL OCR IMPORTS
# ============================================================

try:
    import pytesseract
    from pdf2image import convert_from_path

    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


# ============================================================
# CREATE FLASK APP
# ============================================================

app = Flask(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

app.secret_key = "this-is-my-ai-resume-analyzer-secret-key-2026"

app.config["SESSION_COOKIE_NAME"] = "ResumeIQ_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False

DATABASE = "database/ResumeIQ.db"
UPLOAD_FOLDER = "uploads"

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


# ============================================================
# INITIALIZE DATABASE
# ============================================================

def init_db():

    os.makedirs("database", exist_ok=True)

    connection = get_db()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS resume_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            resume_filename TEXT NOT NULL,
            resume_score INTEGER NOT NULL,
            detected_skills TEXT,
            recommended_jobs TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id)
            REFERENCES users(id)
        )
    """)

    connection.commit()
    connection.close()


# ============================================================
# PDF TEXT EXTRACTION
# ============================================================

def extract_pdf_text(filepath):

    extracted_text = ""

    # --------------------------------------------------------
    # FIRST TRY: PYPDF2
    # --------------------------------------------------------

    try:

        reader = PdfReader(filepath)

        for page in reader.pages:

            try:
                text = page.extract_text()

                if text:
                    extracted_text += text + "\n"

            except Exception:
                continue

    except Exception:
        extracted_text = ""

    # --------------------------------------------------------
    # CLEAN EXTRACTED TEXT
    # --------------------------------------------------------

    extracted_text = extracted_text.strip()

    # --------------------------------------------------------
    # IF ENOUGH TEXT WAS FOUND, RETURN IT
    # --------------------------------------------------------

    if len(extracted_text) >= 80:
        return extracted_text

    # --------------------------------------------------------
    # OCR FALLBACK
    # --------------------------------------------------------

    if OCR_AVAILABLE:

        try:

            images = convert_from_path(filepath)

            ocr_text = ""

            for image in images:

                try:

                    text = pytesseract.image_to_string(image)

                    if text:
                        ocr_text += text + "\n"

                except Exception:
                    continue

            ocr_text = ocr_text.strip()

            if len(ocr_text) > len(extracted_text):
                extracted_text = ocr_text

        except Exception:
            pass

    return extracted_text.strip()


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():
    return render_template("index.html")


# ============================================================
# REGISTER
# ============================================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not name or not email or not password:

            return render_template(
                "register.html",
                error="Please fill in all fields."
            )

        hashed_password = generate_password_hash(password)

        connection = get_db()

        try:

            connection.execute(
                """
                INSERT INTO users
                (name, email, password)
                VALUES (?, ?, ?)
                """,
                (
                    name,
                    email,
                    hashed_password
                )
            )

            connection.commit()
            connection.close()

            return redirect(url_for("login"))

        except sqlite3.IntegrityError:

            connection.close()

            return render_template(
                "register.html",
                error="Email already registered!"
            )

    return render_template("register.html")


# ============================================================
# LOGIN
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        connection = get_db()

        user = connection.execute(
            """
            SELECT *
            FROM users
            WHERE email = ?
            """,
            (email,)
        ).fetchone()

        connection.close()

        if user and check_password_hash(
            user["password"],
            password
        ):

            session.clear()

            session["user_id"] = int(user["id"])
            session["user_name"] = user["name"]
            session["user_email"] = user["email"]

            session.modified = True

            return redirect(url_for("dashboard"))

        return render_template(
            "login.html",
            error="Invalid email or password."
        )

    return render_template("login.html")


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/dashboard")
def dashboard():

    if "user_id" not in session:
        return redirect(url_for("login"))

    connection = get_db()

    latest_analysis = connection.execute(
        """
        SELECT *
        FROM resume_analysis
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (session["user_id"],)
    ).fetchone()

    connection.close()

    resume_status = "Not Analyzed"
    resume_score = "--"
    skills_count = 0
    job_matches = 0

    resume_filename = session.get(
        "resume_filename",
        ""
    )

    if latest_analysis:

        resume_status = "Analyzed"
        resume_score = latest_analysis["resume_score"]

        resume_filename = latest_analysis["resume_filename"]

        try:

            detected_skills = json.loads(
                latest_analysis["detected_skills"]
            )

            skills_count = len(detected_skills)

        except (json.JSONDecodeError, TypeError):

            skills_count = 0

        try:

            recommended_jobs = json.loads(
                latest_analysis["recommended_jobs"]
            )

            job_matches = len(recommended_jobs)

        except (json.JSONDecodeError, TypeError):

            job_matches = 0

    return render_template(
        "dashboard.html",

        user_name=session.get(
            "user_name",
            "User"
        ),

        user_email=session.get(
            "user_email",
            ""
        ),

        resume_filename=resume_filename,

        resume_status=resume_status,

        resume_score=resume_score,

        skills_count=skills_count,

        job_matches=job_matches
    )


# ============================================================
# UPLOAD RESUME
# ============================================================

@app.route("/upload", methods=["GET", "POST"])
def upload():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":

        if "resume" not in request.files:

            return render_template(
                "upload.html",
                error="Please select a resume."
            )

        file = request.files["resume"]

        if file.filename == "":

            return render_template(
                "upload.html",
                error="Please select a resume."
            )

        if not file.filename.lower().endswith(".pdf"):

            return render_template(
                "upload.html",
                error="Only PDF files are allowed."
            )

        os.makedirs(
            app.config["UPLOAD_FOLDER"],
            exist_ok=True
        )

        filename = secure_filename(file.filename)

        base_name, extension = os.path.splitext(filename)

        counter = 1

        filepath = os.path.join(
            app.config["UPLOAD_FOLDER"],
            filename
        )

        while os.path.exists(filepath):

            filename = (
                f"{base_name}_{counter}{extension}"
            )

            filepath = os.path.join(
                app.config["UPLOAD_FOLDER"],
                filename
            )

            counter += 1

        file.save(filepath)

        # ----------------------------------------------------
        # NEW RESUME
        # ----------------------------------------------------

        session["resume_filename"] = filename

        session.pop(
            "detected_skills",
            None
        )

        session.pop(
            "current_analysis_id",
            None
        )

        session["analysis_saved"] = False

        session.modified = True

        return redirect(
            url_for("analysis")
        )

    return render_template("upload.html")


# ============================================================
# RESUME ANALYSIS
# ============================================================

@app.route("/analysis")
def analysis():

    if "user_id" not in session:
        return redirect(url_for("login"))

    resume_filename = session.get(
        "resume_filename",
        ""
    )

    if not resume_filename:
        return redirect(url_for("upload"))

    filepath = os.path.join(
        app.config["UPLOAD_FOLDER"],
        resume_filename
    )

    if not os.path.exists(filepath):

        return render_template(
            "upload.html",
            error="Resume file not found. Please upload again."
        )

    # ========================================================
    # EXTRACT RESUME TEXT
    # ========================================================

    resume_text = extract_pdf_text(filepath)

    resume_lower = resume_text.lower()

    # ========================================================
    # SKILLS DATABASE
    # ========================================================

    skills = [

        "Python",
        "Java",
        "C",
        "C++",
        "C#",

        "HTML",
        "CSS",
        "JavaScript",
        "TypeScript",

        "React",
        "Angular",
        "Vue.js",

        "Node.js",
        "Express.js",

        "Flask",
        "Django",

        "PHP",
        "Laravel",

        "SQL",
        "MySQL",
        "PostgreSQL",
        "MongoDB",

        "Git",
        "GitHub",

        "Figma",
        "UI/UX",

        "Machine Learning",
        "Artificial Intelligence",
        "Data Science",

        "TensorFlow",
        "PyTorch",

        "Power BI",
        "Excel",
        "Tableau",

        "AWS",
        "Azure",
        "Google Cloud",

        "Docker",
        "Kubernetes",

        "Linux",

        "REST API",
        "GraphQL",

        "Cybersecurity",

        "Selenium",
        "Testing",

        "WordPress",

        "Bootstrap",
        "Tailwind CSS",

        "Next.js",
        "Spring Boot",

        "Redis",
        "Firebase",

        "Android",
        "Flutter",

        "Kotlin",
        "Swift",

        "R",
        "MATLAB",

        "NLP",
        "Deep Learning",

        "Computer Vision",

        "Jenkins",
        "Terraform",

        "CI/CD",

        "Agile",
        "Scrum"

    ]

    # ========================================================
    # DETECT SKILLS
    # ========================================================

    detected_skills = []

    for skill in skills:

        skill_pattern = re.escape(
            skill.lower()
        )

        if re.search(
            r"(?<!\w)"
            + skill_pattern
            + r"(?!\w)",
            resume_lower
        ):

            detected_skills.append(skill)

    session["detected_skills"] = detected_skills
    session.modified = True

    # ========================================================
    # SKILL SCORE
    # ========================================================

    total_skills = len(skills)

    detected_count = len(detected_skills)

    if total_skills > 0:

        skill_points = int(
            (detected_count / total_skills) * 40
        )

    else:

        skill_points = 0

    # ========================================================
    # EDUCATION
    # ========================================================

    education_keywords = [

        "education",
        "bachelor",
        "master",
        "degree",
        "b.e",
        "b.tech",
        "m.e",
        "m.tech",
        "bsc",
        "msc",
        "bca",
        "mca",
        "computer science",
        "engineering",
        "university",
        "college",
        "diploma",
        "school",
        "bachelor of engineering",
        "bachelor of technology",
        "master of engineering",
        "master of technology",
        "cgpa",
        "gpa"

    ]

    education_found = any(
        keyword in resume_lower
        for keyword in education_keywords
    )

    education_score = 15 if education_found else 0

    # ========================================================
    # EXPERIENCE
    # ========================================================

    experience_keywords = [

        "experience",
        "internship",
        "intern",
        "developer",
        "worked",
        "company",
        "employment",
        "work experience",
        "professional experience",
        "software engineer",
        "freelance",
        "freelancer"

    ]

    experience_found = any(
        keyword in resume_lower
        for keyword in experience_keywords
    )

    experience_score = 15 if experience_found else 0

    # ========================================================
    # PROJECT
    # ========================================================

    project_keywords = [

        "project",
        "projects",
        "developed",
        "created",
        "built",
        "application",
        "website",
        "system",
        "implementation",
        "prototype"

    ]

    project_found = any(
        keyword in resume_lower
        for keyword in project_keywords
    )

    project_score = 10 if project_found else 0

    # ========================================================
    # CERTIFICATIONS
    # ========================================================

    certification_keywords = [

        "certification",
        "certifications",
        "certificate",
        "certificates",
        "certified",
        "coursera",
        "udemy",
        "linkedin learning",
        "google certification",
        "aws certification",
        "microsoft certification",
        "oracle certification",
        "nptel",
        "great learning"

    ]

    certification_found = any(
        keyword in resume_lower
        for keyword in certification_keywords
    )

    certification_score = (
        10 if certification_found else 0
    )

    # ========================================================
    # CONTACT INFORMATION
    # ========================================================

    email_pattern = (
        r"[A-Za-z0-9._%+-]+"
        r"@[A-Za-z0-9.-]+"
        r"\.[A-Za-z]{2,}"
    )

    email_found = re.search(
        email_pattern,
        resume_text
    ) is not None

    phone_pattern = (
        r"(\+?\d[\d\s\-()]{8,}\d)"
    )

    phone_found = re.search(
        phone_pattern,
        resume_text
    ) is not None

    profile_keywords = [

        "linkedin",
        "github",
        "portfolio",
        "contact",
        "mobile",
        "phone",
        "email"

    ]

    profile_found = any(
        keyword in resume_lower
        for keyword in profile_keywords
    )

    contact_found = (
        email_found
        or phone_found
        or profile_found
    )

    contact_score = (
        10 if contact_found else 0
    )

    # ========================================================
    # FINAL SCORE
    # ========================================================

    score = (

        skill_points
        + education_score
        + experience_score
        + project_score
        + certification_score
        + contact_score

    )

    score = max(
        0,
        min(score, 100)
    )

    # ========================================================
    # RECOMMENDED SKILLS
    # ========================================================

    recommended_skills = [

        skill
        for skill in skills
        if skill not in detected_skills

    ]

    recommended_skills = (
        recommended_skills[:10]
    )

    # ========================================================
    # BASIC JOB ROLES
    # ========================================================

    job_roles = []

    if (
        "Python" in detected_skills
        and "SQL" in detected_skills
    ):
        job_roles.append(
            "Python Developer"
        )

    if (
        "HTML" in detected_skills
        and "CSS" in detected_skills
        and "JavaScript" in detected_skills
    ):
        job_roles.append(
            "Frontend Developer"
        )

    if "JavaScript" in detected_skills:

        job_roles.append(
            "JavaScript Developer"
        )

    if "React" in detected_skills:

        job_roles.append(
            "React Developer"
        )

    if (
        "SQL" in detected_skills
        or "MySQL" in detected_skills
    ):

        job_roles.append(
            "Database Developer"
        )

    if (
        "Machine Learning"
        in detected_skills
        or "TensorFlow"
        in detected_skills
        or "PyTorch"
        in detected_skills
    ):

        job_roles.append(
            "Machine Learning Engineer"
        )

    if (
        "UI/UX" in detected_skills
        or "Figma" in detected_skills
    ):

        job_roles.append(
            "UI/UX Designer"
        )

    if "Java" in detected_skills:

        job_roles.append(
            "Java Developer"
        )

    if (
        "Node.js" in detected_skills
        or "Express.js" in detected_skills
    ):

        job_roles.append(
            "Backend Developer"
        )

    if not job_roles:

        job_roles = [

            "Software Developer",
            "Web Developer",
            "Junior Developer"

        ]

    # ========================================================
    # AI IMPROVEMENT SUGGESTIONS
    # ========================================================

    suggestions = []

    if not education_found:

        suggestions.append(
            "Add your educational qualification."
        )

    if not experience_found:

        suggestions.append(
            "Add internship or work experience."
        )

    if not project_found:

        suggestions.append(
            "Add at least one academic or personal project."
        )

    if not certification_found:

        suggestions.append(
            "Add relevant certifications to strengthen your resume."
        )

    if not contact_found:

        suggestions.append(
            "Add contact information such as email, phone, LinkedIn or GitHub."
        )

    if len(detected_skills) < 5:

        suggestions.append(
            "Add more technical skills relevant to your career."
        )

    if len(resume_text.strip()) < 500:

        suggestions.append(
            "Your resume appears short. Consider adding more relevant details."
        )

    if not resume_text.strip():

        suggestions.append(
            "No readable text was extracted from the PDF. "
            "Try exporting the resume as a text-based PDF."
        )

    if not suggestions:

        suggestions.append(
            "Your resume has a good basic structure. "
            "Keep your skills and projects updated."
        )

    # ========================================================
    # SAVE ANALYSIS
    # ========================================================

    if not session.get("analysis_saved", False):

        connection = get_db()

        connection.execute(
            """
            INSERT INTO resume_analysis
            (
                user_id,
                resume_filename,
                resume_score,
                detected_skills,
                recommended_jobs
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session["user_id"],
                resume_filename,
                score,
                json.dumps(detected_skills),
                json.dumps(job_roles)
            )
        )

        connection.commit()

        new_analysis = connection.execute(
            """
            SELECT id
            FROM resume_analysis
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                session["user_id"],
            )
        ).fetchone()

        connection.close()

        if new_analysis:

            session["current_analysis_id"] = int(
                new_analysis["id"]
            )

        session["analysis_saved"] = True

        session.modified = True

    # ========================================================
    # RENDER ANALYSIS PAGE
    # ========================================================

    return render_template(

        "analysis.html",

        resume_text=resume_text,

        resume_filename=resume_filename,

        detected_skills=detected_skills,

        recommended_skills=recommended_skills,

        job_roles=job_roles,

        suggestions=suggestions,

        score=score,

        education_found=education_found,

        experience_found=experience_found,

        project_found=project_found,

        certification_found=certification_found,

        contact_found=contact_found

    )


# ============================================================
# JOB RECOMMENDATIONS
# ============================================================

@app.route("/jobs")
def jobs():

    if "user_id" not in session:
        return redirect(url_for("login"))

    analysis_id = request.args.get(
        "analysis_id",
        type=int
    )

    connection = get_db()

    if analysis_id:

        analysis_data = connection.execute(
            """
            SELECT *
            FROM resume_analysis
            WHERE id = ?
            AND user_id = ?
            """,
            (
                analysis_id,
                session["user_id"]
            )
        ).fetchone()

    else:

        analysis_data = connection.execute(
            """
            SELECT *
            FROM resume_analysis
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (
                session["user_id"],
            )
        ).fetchone()

    connection.close()

    if not analysis_data:

        return render_template(
            "jobs.html",
            jobs=[],
            resume_filename="",
            analysis_id=None
        )

    try:

        detected_skills = json.loads(
            analysis_data["detected_skills"]
        )

    except (json.JSONDecodeError, TypeError):

        detected_skills = []

    # ========================================================
    # 50+ JOB DATABASE
    # ========================================================

    jobs_data = [

        {
            "title": "Python Developer",
            "description": "Develop applications, automation tools and backend systems using Python.",
            "skills": ["Python", "SQL", "Flask"],
            "required": ["Python", "SQL"]
        },

        {
            "title": "Frontend Developer",
            "description": "Build responsive and interactive websites using modern frontend technologies.",
            "skills": ["HTML", "CSS", "JavaScript", "React"],
            "required": ["HTML", "CSS", "JavaScript"]
        },

        {
            "title": "Full Stack Developer",
            "description": "Develop complete web applications covering frontend, backend and databases.",
            "skills": ["HTML", "CSS", "JavaScript", "Python", "SQL"],
            "required": ["HTML", "CSS", "JavaScript", "Python"]
        },

        {
            "title": "UI/UX Designer",
            "description": "Create user-friendly interfaces, wireframes and digital experiences.",
            "skills": ["Figma", "UI/UX"],
            "required": ["Figma", "UI/UX"]
        },

        {
            "title": "Data Analyst",
            "description": "Analyze datasets and create meaningful business insights.",
            "skills": ["Python", "SQL", "Excel", "Power BI"],
            "required": ["Python", "SQL", "Excel"]
        },

        {
            "title": "Machine Learning Engineer",
            "description": "Build and deploy machine learning models.",
            "skills": ["Python", "Machine Learning", "TensorFlow"],
            "required": ["Python", "Machine Learning"]
        },

        {
            "title": "Backend Developer",
            "description": "Create server-side applications, APIs and backend services.",
            "skills": ["Python", "Flask", "SQL", "Django"],
            "required": ["Python", "SQL"]
        },

        {
            "title": "Java Developer",
            "description": "Develop enterprise and backend applications using Java.",
            "skills": ["Java", "SQL", "Git"],
            "required": ["Java", "SQL"]
        },

        {
            "title": "React Developer",
            "description": "Build modern interactive user interfaces using React.",
            "skills": ["React", "JavaScript", "HTML", "CSS"],
            "required": ["React", "JavaScript"]
        },

        {
            "title": "Angular Developer",
            "description": "Develop scalable web applications using Angular.",
            "skills": ["Angular", "JavaScript", "HTML", "CSS"],
            "required": ["Angular", "JavaScript"]
        },

        {
            "title": "Node.js Developer",
            "description": "Develop scalable backend services and APIs.",
            "skills": ["Node.js", "JavaScript", "Express.js", "MongoDB"],
            "required": ["Node.js", "JavaScript"]
        },

        {
            "title": "PHP Developer",
            "description": "Develop dynamic websites and server-side applications.",
            "skills": ["PHP", "MySQL", "HTML", "CSS"],
            "required": ["PHP", "MySQL"]
        },

        {
            "title": "Database Developer",
            "description": "Design and maintain databases.",
            "skills": ["SQL", "MySQL", "PostgreSQL", "MongoDB"],
            "required": ["SQL", "MySQL"]
        },

        {
            "title": "Data Scientist",
            "description": "Use statistics, programming and machine learning to discover insights.",
            "skills": ["Python", "Data Science", "Machine Learning", "SQL"],
            "required": ["Python", "Data Science"]
        },

        {
            "title": "AI Engineer",
            "description": "Develop intelligent applications using artificial intelligence.",
            "skills": ["Python", "Artificial Intelligence", "Machine Learning"],
            "required": ["Python", "Artificial Intelligence"]
        },

        {
            "title": "Cloud Engineer",
            "description": "Design and manage cloud infrastructure.",
            "skills": ["AWS", "Linux", "Docker"],
            "required": ["AWS", "Linux"]
        },

        {
            "title": "DevOps Engineer",
            "description": "Automate development and deployment processes.",
            "skills": ["Docker", "Kubernetes", "AWS", "Linux"],
            "required": ["Docker", "AWS"]
        },

        {
            "title": "Cybersecurity Analyst",
            "description": "Monitor systems and identify security threats.",
            "skills": ["Cybersecurity", "Linux", "Python"],
            "required": ["Cybersecurity", "Linux"]
        },

        {
            "title": "QA Testing Engineer",
            "description": "Test software applications and identify bugs.",
            "skills": ["Testing", "Selenium", "Java", "Python"],
            "required": ["Testing", "Selenium"]
        },

        {
            "title": "Automation Test Engineer",
            "description": "Create automated testing solutions.",
            "skills": ["Selenium", "Python", "Testing"],
            "required": ["Selenium", "Testing"]
        },

        {
            "title": "Business Intelligence Analyst",
            "description": "Transform business data into useful insights.",
            "skills": ["Power BI", "Excel", "SQL"],
            "required": ["Power BI", "SQL"]
        },

        {
            "title": "Excel Data Analyst",
            "description": "Analyze business data using Excel.",
            "skills": ["Excel", "SQL", "Power BI"],
            "required": ["Excel", "SQL"]
        },

        {
            "title": "Cloud Application Developer",
            "description": "Build scalable applications on cloud platforms.",
            "skills": ["Python", "AWS", "Docker"],
            "required": ["Python", "AWS"]
        },

        {
            "title": "Web Developer",
            "description": "Create responsive websites and web applications.",
            "skills": ["HTML", "CSS", "JavaScript"],
            "required": ["HTML", "CSS"]
        },

        {
            "title": "Software Developer",
            "description": "Design and develop software applications.",
            "skills": ["Python", "Java", "C++", "Git"],
            "required": ["Python", "Git"]
        },

        {
            "title": "C++ Developer",
            "description": "Develop high-performance applications using C++.",
            "skills": ["C++", "Git", "Linux"],
            "required": ["C++", "Git"]
        },

        {
            "title": "WordPress Developer",
            "description": "Build and customize websites using WordPress.",
            "skills": ["WordPress", "HTML", "CSS", "JavaScript"],
            "required": ["WordPress", "HTML"]
        },

        {
            "title": "API Developer",
            "description": "Design and develop REST APIs.",
            "skills": ["REST API", "Python", "Node.js", "SQL"],
            "required": ["REST API", "Python"]
        },

        {
            "title": "JavaScript Developer",
            "description": "Build web applications using JavaScript.",
            "skills": ["JavaScript", "HTML", "CSS"],
            "required": ["JavaScript", "HTML"]
        },

        {
            "title": "React Frontend Engineer",
            "description": "Develop modern frontend applications using React.",
            "skills": ["React", "JavaScript", "HTML", "CSS"],
            "required": ["React", "JavaScript"]
        },

        {
            "title": "Django Developer",
            "description": "Develop backend applications using Django.",
            "skills": ["Python", "Django", "SQL"],
            "required": ["Python", "Django"]
        },

        {
            "title": "Flask Developer",
            "description": "Develop lightweight web applications using Flask.",
            "skills": ["Python", "Flask", "SQL"],
            "required": ["Python", "Flask"]
        },

        {
            "title": "MongoDB Developer",
            "description": "Build applications using MongoDB databases.",
            "skills": ["MongoDB", "Node.js", "JavaScript"],
            "required": ["MongoDB"]
        },

        {
            "title": "SQL Developer",
            "description": "Develop queries, databases and data solutions.",
            "skills": ["SQL", "MySQL", "PostgreSQL"],
            "required": ["SQL"]
        },

        {
            "title": "PostgreSQL Developer",
            "description": "Develop database solutions using PostgreSQL.",
            "skills": ["PostgreSQL", "SQL", "Python"],
            "required": ["PostgreSQL", "SQL"]
        },

        {
            "title": "MongoDB Database Engineer",
            "description": "Manage NoSQL database systems.",
            "skills": ["MongoDB", "Node.js"],
            "required": ["MongoDB"]
        },

        {
            "title": "Figma UI Designer",
            "description": "Design user interfaces and prototypes using Figma.",
            "skills": ["Figma", "UI/UX"],
            "required": ["Figma"]
        },

        {
            "title": "UX Designer",
            "description": "Research users and design better digital experiences.",
            "skills": ["UI/UX", "Figma"],
            "required": ["UI/UX"]
        },

        {
            "title": "Product Designer",
            "description": "Design complete digital products and user experiences.",
            "skills": ["Figma", "UI/UX", "HTML", "CSS"],
            "required": ["Figma", "UI/UX"]
        },

        {
            "title": "Data Engineer",
            "description": "Build data pipelines and data processing systems.",
            "skills": ["Python", "SQL", "AWS"],
            "required": ["Python", "SQL"]
        },

        {
            "title": "Deep Learning Engineer",
            "description": "Develop deep learning models and AI systems.",
            "skills": ["Python", "Deep Learning", "TensorFlow", "PyTorch"],
            "required": ["Python", "Deep Learning"]
        },

        {
            "title": "NLP Engineer",
            "description": "Develop natural language processing applications.",
            "skills": ["Python", "NLP", "Machine Learning"],
            "required": ["Python", "NLP"]
        },

        {
            "title": "Computer Vision Engineer",
            "description": "Develop image and video analysis systems.",
            "skills": ["Python", "Computer Vision", "Machine Learning"],
            "required": ["Python", "Computer Vision"]
        },

        {
            "title": "AI/ML Developer",
            "description": "Develop artificial intelligence and machine learning applications.",
            "skills": ["Python", "AI", "Machine Learning"],
            "required": ["Python", "Machine Learning"]
        },

        {
            "title": "AWS Cloud Developer",
            "description": "Develop and deploy applications using AWS.",
            "skills": ["AWS", "Python", "Docker"],
            "required": ["AWS"]
        },

        {
            "title": "Azure Cloud Engineer",
            "description": "Build and manage applications on Microsoft Azure.",
            "skills": ["Azure", "Linux", "Docker"],
            "required": ["Azure"]
        },

        {
            "title": "Google Cloud Developer",
            "description": "Develop cloud applications using Google Cloud.",
            "skills": ["Google Cloud", "Python", "Docker"],
            "required": ["Google Cloud"]
        },

        {
            "title": "Docker Engineer",
            "description": "Build and manage containerized applications.",
            "skills": ["Docker", "Linux", "Kubernetes"],
            "required": ["Docker"]
        },

        {
            "title": "Kubernetes Engineer",
            "description": "Manage container orchestration and cloud infrastructure.",
            "skills": ["Kubernetes", "Docker", "Linux"],
            "required": ["Kubernetes"]
        },

        {
            "title": "Linux System Administrator",
            "description": "Manage Linux servers and infrastructure.",
            "skills": ["Linux", "AWS", "Docker"],
            "required": ["Linux"]
        },

        {
            "title": "Git Developer",
            "description": "Work with collaborative software development workflows.",
            "skills": ["Git", "GitHub"],
            "required": ["Git"]
        },

        {
            "title": "DevSecOps Engineer",
            "description": "Combine DevOps automation with cybersecurity practices.",
            "skills": ["Docker", "AWS", "Linux", "Cybersecurity"],
            "required": ["Docker", "Cybersecurity"]
        },

        {
            "title": "Selenium Automation Developer",
            "description": "Develop automated browser testing solutions.",
            "skills": ["Selenium", "Python", "Testing"],
            "required": ["Selenium"]
        },

        {
            "title": "Software QA Engineer",
            "description": "Perform manual and automated software testing.",
            "skills": ["Testing", "Selenium", "Java"],
            "required": ["Testing"]
        },

        {
            "title": "Test Automation Developer",
            "description": "Develop automated testing frameworks.",
            "skills": ["Testing", "Selenium", "Python"],
            "required": ["Testing"]
        },

        {
            "title": "Mobile App Developer",
            "description": "Develop applications for mobile platforms.",
            "skills": ["Java", "Kotlin", "Flutter"],
            "required": ["Java"]
        },

        {
            "title": "Android Developer",
            "description": "Build Android applications.",
            "skills": ["Java", "Kotlin"],
            "required": ["Java"]
        },

        {
            "title": "Flutter Developer",
            "description": "Build cross-platform mobile applications using Flutter.",
            "skills": ["Flutter", "Dart"],
            "required": ["Flutter"]
        },

        {
            "title": "Kotlin Developer",
            "description": "Develop modern Android applications using Kotlin.",
            "skills": ["Kotlin", "Java"],
            "required": ["Kotlin"]
        },

        {
            "title": "R Data Analyst",
            "description": "Analyze and visualize data using R.",
            "skills": ["R", "Data Science", "SQL"],
            "required": ["R"]
        },

        {
            "title": "MATLAB Developer",
            "description": "Develop engineering and analytical solutions using MATLAB.",
            "skills": ["MATLAB", "Python"],
            "required": ["MATLAB"]
        },

        {
            "title": "BI Developer",
            "description": "Build dashboards and business intelligence solutions.",
            "skills": ["Power BI", "SQL", "Excel"],
            "required": ["Power BI"]
        },

        {
            "title": "Tableau Developer",
            "description": "Build data visualization dashboards using Tableau.",
            "skills": ["Tableau", "SQL", "Excel"],
            "required": ["Tableau"]
        },

        {
            "title": "Excel Business Analyst",
            "description": "Analyze business requirements and prepare reports.",
            "skills": ["Excel", "Power BI", "SQL"],
            "required": ["Excel"]
        },

        {
            "title": "Cloud DevOps Developer",
            "description": "Build automated cloud deployment solutions.",
            "skills": ["AWS", "Docker", "Kubernetes", "Linux"],
            "required": ["AWS", "Docker"]
        },

        {
            "title": "Infrastructure Engineer",
            "description": "Manage servers, cloud infrastructure and deployments.",
            "skills": ["Linux", "AWS", "Docker"],
            "required": ["Linux", "AWS"]
        },

        {
            "title": "Terraform Cloud Engineer",
            "description": "Automate infrastructure using infrastructure-as-code.",
            "skills": ["AWS", "Terraform", "Docker"],
            "required": ["AWS"]
        },

        {
            "title": "CI/CD Engineer",
            "description": "Automate software build and deployment pipelines.",
            "skills": ["Jenkins", "Docker", "Git", "CI/CD"],
            "required": ["Git", "Docker"]
        },

        {
            "title": "Jenkins DevOps Engineer",
            "description": "Build automated CI/CD pipelines using Jenkins.",
            "skills": ["Jenkins", "Docker", "Git"],
            "required": ["Jenkins"]
        },

        {
            "title": "Agile Software Developer",
            "description": "Develop software using Agile development methodologies.",
            "skills": ["Agile", "Scrum", "Git", "Java"],
            "required": ["Agile"]
        },

        {
            "title": "Scrum Software Developer",
            "description": "Work with software teams following Scrum practices.",
            "skills": ["Scrum", "Agile", "Git"],
            "required": ["Scrum"]
        },

        {
            "title": "Next.js Developer",
            "description": "Build modern React-based web applications using Next.js.",
            "skills": ["Next.js", "React", "JavaScript"],
            "required": ["Next.js", "React"]
        },

        {
            "title": "TypeScript Developer",
            "description": "Develop scalable applications using TypeScript.",
            "skills": ["TypeScript", "JavaScript", "React"],
            "required": ["TypeScript"]
        },

        {
            "title": "Vue.js Developer",
            "description": "Build modern web interfaces using Vue.js.",
            "skills": ["Vue.js", "JavaScript", "HTML", "CSS"],
            "required": ["Vue.js"]
        },

        {
            "title": "Spring Boot Developer",
            "description": "Develop Java backend applications using Spring Boot.",
            "skills": ["Java", "Spring Boot", "SQL"],
            "required": ["Java", "Spring Boot"]
        },

        {
            "title": "Laravel Developer",
            "description": "Develop PHP applications using Laravel.",
            "skills": ["PHP", "Laravel", "MySQL"],
            "required": ["PHP", "Laravel"]
        },

        {
            "title": "Tailwind CSS Developer",
            "description": "Build responsive interfaces using Tailwind CSS.",
            "skills": ["Tailwind CSS", "HTML", "CSS", "JavaScript"],
            "required": ["Tailwind CSS"]
        },

        {
            "title": "Bootstrap Developer",
            "description": "Create responsive websites using Bootstrap.",
            "skills": ["Bootstrap", "HTML", "CSS", "JavaScript"],
            "required": ["Bootstrap"]
        },

        {
            "title": "Firebase Developer",
            "description": "Develop web and mobile applications using Firebase.",
            "skills": ["Firebase", "JavaScript", "React"],
            "required": ["Firebase"]
        },

        {
            "title": "GraphQL Developer",
            "description": "Develop APIs and applications using GraphQL.",
            "skills": ["GraphQL", "JavaScript", "Node.js"],
            "required": ["GraphQL"]
        },

        {
            "title": "REST API Developer",
            "description": "Develop RESTful APIs for applications.",
            "skills": ["REST API", "Python", "Node.js"],
            "required": ["REST API"]
        },

        {
            "title": "C# Developer",
            "description": "Develop software applications using C#.",
            "skills": ["C#", "SQL", "Git"],
            "required": ["C#"]
        },

        {
            "title": "C Developer",
            "description": "Develop system and software applications using C.",
            "skills": ["C", "Linux", "Git"],
            "required": ["C"]
        },

        {
            "title": "Cybersecurity Engineer",
            "description": "Protect systems and applications from security threats.",
            "skills": ["Cybersecurity", "Linux", "Python"],
            "required": ["Cybersecurity"]
        },

        {
            "title": "Security Analyst",
            "description": "Analyze systems and security incidents.",
            "skills": ["Cybersecurity", "Linux"],
            "required": ["Cybersecurity"]
        },

        {
            "title": "WordPress Web Developer",
            "description": "Create and customize WordPress websites.",
            "skills": ["WordPress", "HTML", "CSS", "JavaScript"],
            "required": ["WordPress"]
        }

    ]

    # ========================================================
    # BETTER JOB MATCHING
    # ========================================================

    recommended_jobs = []

    detected_skill_set = {
        skill.lower()
        for skill in detected_skills
    }

    for job in jobs_data:

        required_skills = job.get(
            "required",
            []
        )

        all_job_skills = job.get(
            "skills",
            []
        )

        required_matches = [

            skill
            for skill in required_skills
            if skill.lower()
            in detected_skill_set

        ]

        all_matches = [

            skill
            for skill in all_job_skills
            if skill.lower()
            in detected_skill_set

        ]

        if required_skills:

            required_percentage = (

                len(required_matches)
                / len(required_skills)

            ) * 100

        else:

            required_percentage = 0

        if all_job_skills:

            overall_percentage = (

                len(all_matches)
                / len(all_job_skills)

            ) * 100

        else:

            overall_percentage = 0

        match_percentage = int(

            (required_percentage * 0.70)
            + (overall_percentage * 0.30)

        )

        if match_percentage > 0:

            job_copy = job.copy()

            job_copy["match"] = match_percentage

            job_copy["matched_skills"] = all_matches

            recommended_jobs.append(
                job_copy
            )

    # ========================================================
    # SORT
    # ========================================================

    recommended_jobs.sort(

        key=lambda job: job["match"],

        reverse=True

    )

    # ========================================================
    # LIMIT
    # ========================================================

    recommended_jobs = recommended_jobs[:15]

    # ========================================================
    # IF NO MATCH
    # ========================================================

    if not recommended_jobs:

        recommended_jobs = [

            {
                "title": "Software Developer",
                "description": "General software development opportunity.",
                "skills": ["Programming"],
                "required": [],
                "match": 10,
                "matched_skills": []
            },

            {
                "title": "Junior Web Developer",
                "description": "Entry-level web development opportunity.",
                "skills": ["HTML", "CSS"],
                "required": [],
                "match": 10,
                "matched_skills": []
            },

            {
                "title": "Junior Software Engineer",
                "description": "Entry-level software engineering opportunity.",
                "skills": ["Programming"],
                "required": [],
                "match": 10,
                "matched_skills": []
            }

        ]

    return render_template(

        "jobs.html",

        jobs=recommended_jobs,

        resume_filename=analysis_data[
            "resume_filename"
        ],

        analysis_id=analysis_data["id"]

    )


# ============================================================
# ANALYSIS HISTORY
# ============================================================

@app.route("/history")
def history():

    if "user_id" not in session:
        return redirect(url_for("login"))

    connection = get_db()

    analyses = connection.execute(
        """
        SELECT *
        FROM resume_analysis
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (
            session["user_id"],
        )
    ).fetchall()

    connection.close()

    history_data = []

    for analysis_item in analyses:

        item = dict(analysis_item)

        try:

            item["detected_skills"] = json.loads(
                item["detected_skills"]
            )

        except (json.JSONDecodeError, TypeError):

            item["detected_skills"] = []

        try:

            item["recommended_jobs"] = json.loads(
                item["recommended_jobs"]
            )

        except (json.JSONDecodeError, TypeError):

            item["recommended_jobs"] = []

        history_data.append(item)

    return render_template(
        "history.html",
        analyses=history_data
    )


# ============================================================
# DELETE ANALYSIS HISTORY
# ============================================================

@app.route(
    "/delete_history/<int:analysis_id>",
    methods=["POST"]
)
def delete_history(analysis_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    connection = get_db()

    connection.execute(
        """
        DELETE FROM resume_analysis
        WHERE id = ?
        AND user_id = ?
        """,
        (
            analysis_id,
            session["user_id"]
        )
    )

    connection.commit()
    connection.close()

    if session.get(
        "current_analysis_id"
    ) == analysis_id:

        session.pop(
            "current_analysis_id",
            None
        )

        session.modified = True

    return redirect(
        url_for("history")
    )


# ============================================================
# AI RESUME BUILDER
# ============================================================

@app.route(
    "/resume-builder",
    methods=["GET", "POST"]
)
def resume_builder():

    if "user_id" not in session:
        return redirect(url_for("login"))

    resume = None

    if request.method == "POST":

        name = request.form.get(
            "name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip()

        phone = request.form.get(
            "phone",
            ""
        ).strip()

        location = request.form.get(
            "location",
            ""
        ).strip()

        linkedin = request.form.get(
            "linkedin",
            ""
        ).strip()

        github = request.form.get(
            "github",
            ""
        ).strip()

        career = request.form.get(
            "career",
            ""
        ).strip()

        summary = request.form.get(
            "summary",
            ""
        ).strip()

        skills = request.form.get(
            "skills",
            ""
        ).strip()

        education = request.form.get(
            "education",
            ""
        ).strip()

        experience = request.form.get(
            "experience",
            ""
        ).strip()

        projects = request.form.get(
            "projects",
            ""
        ).strip()

        certifications = request.form.get(
            "certifications",
            ""
        ).strip()

        if not name or not email:

            return render_template(

                "resume_builder.html",

                resume={

                    "name": name,
                    "email": email,
                    "phone": phone,
                    "location": location,
                    "linkedin": linkedin,
                    "github": github,
                    "career": career,
                    "summary": summary,
                    "skills": skills,
                    "education": education,
                    "experience": experience,
                    "projects": projects,
                    "certifications": certifications

                },

                error="Name and email are required."

            )

        resume = {

            "name": name,
            "email": email,
            "phone": phone,
            "location": location,
            "linkedin": linkedin,
            "github": github,
            "career": career,
            "summary": summary,
            "skills": skills,
            "education": education,
            "experience": experience,
            "projects": projects,
            "certifications": certifications

        }

        session["builder_resume"] = resume

        session.modified = True

    if resume is None:

        resume = session.get(
            "builder_resume"
        )

    return render_template(
        "resume_builder.html",
        resume=resume
    )


# ============================================================
# CLEAR RESUME BUILDER
# ============================================================

@app.route("/clear-builder")
def clear_builder():

    if "user_id" not in session:
        return redirect(url_for("login"))

    session.pop(
        "builder_resume",
        None
    )

    session.modified = True

    return redirect(
        url_for("resume_builder")
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("home")
    )


# ============================================================
# RUN APPLICATION
# ============================================================

if __name__ == "__main__":

    init_db()

    os.makedirs(
        UPLOAD_FOLDER,
        exist_ok=True
    )

    app.run(
        debug=True
    )