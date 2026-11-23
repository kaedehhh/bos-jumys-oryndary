"""
Веб-қосымша: жұмыс орындарын іздеу жүйесі.
Маңызды: бұл бағдарлама бар vacancies.db файлын қолданады және
кестелерді қайта құрмайды, өшірмейді немесе бастапқы деректерді өзгертпейді.
Іске қосу: python main.py
"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import json
import sqlite3
import webbrowser
import threading

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "docs"
DB_PATH = WEB_DIR / "vacancies.db"
HOST = "127.0.0.1"
PORT = 8000

REQUIRED_TABLES = {
    "Users", "Companies", "Vacancies", "Skills",
    "User_Skills", "Vacancy_Skills", "Applications"
}


def connect_db():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Деректер базасы табылмады: {DB_PATH}\n"
            "Өз курстық жұмысыңыздағы vacancies.db файлын docs бумасына көшіріңіз."
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    existing = {
        row["name"] for row in
        conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing = REQUIRED_TABLES - existing
    if missing:
        conn.close()
        raise RuntimeError(
            "Деректер базасында қажетті кестелер жоқ: " + ", ".join(sorted(missing))
        )
    return conn


def get_vacancies(params):
    q = params.get("q", [""])[0].strip()
    city = params.get("city", [""])[0].strip()
    min_salary = params.get("min_salary", [""])[0].strip()
    max_salary = params.get("max_salary", [""])[0].strip()
    experience = params.get("experience", [""])[0].strip()

    sql = """
        SELECT v.vacancy_id, v.title, v.description, v.city, v.salary,
               v.experience_years, c.company_name, c.industry,
               c.email AS company_email,
               GROUP_CONCAT(s.skill_name, ', ') AS skills
        FROM Vacancies v
        LEFT JOIN Companies c ON c.company_id = v.company_id
        LEFT JOIN Vacancy_Skills vs ON vs.vacancy_id = v.vacancy_id
        LEFT JOIN Skills s ON s.skill_id = vs.skill_id
        WHERE 1=1
    """
    values = []
    if q:
        sql += " AND (v.title LIKE ? OR v.description LIKE ? OR c.company_name LIKE ?)"
        term = f"%{q}%"
        values.extend([term, term, term])
    if city:
        sql += " AND v.city = ?"
        values.append(city)
    if min_salary:
        sql += " AND v.salary >= ?"
        values.append(float(min_salary))
    if max_salary:
        sql += " AND v.salary <= ?"
        values.append(float(max_salary))
    if experience:
        sql += " AND v.experience_years <= ?"
        values.append(int(experience))
    sql += """
        GROUP BY v.vacancy_id
        ORDER BY v.salary DESC, v.title
    """

    with connect_db() as conn:
        rows = conn.execute(sql, values).fetchall()
        cities = [
            row["city"] for row in
            conn.execute("SELECT DISTINCT city FROM Vacancies WHERE city IS NOT NULL ORDER BY city")
        ]
        total = conn.execute("SELECT COUNT(*) FROM Vacancies").fetchone()[0]
    return {
        "items": [dict(row) for row in rows],
        "cities": cities,
        "total": total,
        "found": len(rows)
    }


def get_vacancy(vacancy_id):
    sql = """
        SELECT v.vacancy_id, v.title, v.description, v.city, v.salary,
               v.experience_years, c.company_name, c.industry,
               c.email AS company_email,
               GROUP_CONCAT(s.skill_name, ', ') AS skills
        FROM Vacancies v
        LEFT JOIN Companies c ON c.company_id = v.company_id
        LEFT JOIN Vacancy_Skills vs ON vs.vacancy_id = v.vacancy_id
        LEFT JOIN Skills s ON s.skill_id = vs.skill_id
        WHERE v.vacancy_id = ?
        GROUP BY v.vacancy_id
    """
    with connect_db() as conn:
        row = conn.execute(sql, (vacancy_id,)).fetchone()
    return dict(row) if row else None


def submit_application(data):
    full_name = str(data.get("full_name", "")).strip()
    email = str(data.get("email", "")).strip()
    phone = str(data.get("phone", "")).strip()
    city = str(data.get("city", "")).strip()
    vacancy_id = data.get("vacancy_id")

    if not full_name or not email or not vacancy_id:
        raise ValueError("Аты-жөніңізді, email және вакансияны толтырыңыз.")
    if "@" not in email or "." not in email.split("@")[-1]:
        raise ValueError("Email мекенжайын дұрыс енгізіңіз.")

    with connect_db() as conn:
        vacancy = conn.execute(
            "SELECT vacancy_id FROM Vacancies WHERE vacancy_id = ?", (vacancy_id,)
        ).fetchone()
        if not vacancy:
            raise ValueError("Бұл вакансия табылмады.")

        user = conn.execute(
            "SELECT user_id FROM Users WHERE email = ? ORDER BY user_id LIMIT 1",
            (email,)
        ).fetchone()
        if user:
            user_id = user["user_id"]
        else:
            cur = conn.execute(
                """INSERT INTO Users (full_name, email, phone, city, experience_years)
                   VALUES (?, ?, ?, ?, ?)""",
                (full_name, email, phone, city, 0)
            )
            user_id = cur.lastrowid

        duplicate = conn.execute(
            """SELECT application_id FROM Applications
               WHERE user_id = ? AND vacancy_id = ?""",
            (user_id, vacancy_id)
        ).fetchone()
        if duplicate:
            return {"ok": True, "message": "Сіз бұл вакансияға бұрын өтінім жібергенсіз."}

        conn.execute(
            """INSERT INTO Applications
               (user_id, vacancy_id, application_date, status)
               VALUES (?, ?, date('now'), ?)""",
            (user_id, vacancy_id, "Қаралуда")
        )
        conn.commit()
    return {"ok": True, "message": "Өтініміңіз сәтті жіберілді!"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/vacancies":
            try:
                self.send_json(get_vacancies(parse_qs(parsed.query)))
            except Exception as error:
                self.send_json({"error": str(error)}, 500)
            return
        if parsed.path.startswith("/api/vacancies/"):
            try:
                vacancy_id = int(parsed.path.rsplit("/", 1)[-1])
                item = get_vacancy(vacancy_id)
                if item:
                    self.send_json(item)
                else:
                    self.send_json({"error": "Вакансия табылмады."}, 404)
            except ValueError:
                self.send_json({"error": "Вакансия ID қате."}, 400)
            except Exception as error:
                self.send_json({"error": str(error)}, 500)
            return
        if parsed.path == "/api/stats":
            try:
                with connect_db() as conn:
                    stats = {
                        "vacancies": conn.execute("SELECT COUNT(*) FROM Vacancies").fetchone()[0],
                        "companies": conn.execute("SELECT COUNT(*) FROM Companies").fetchone()[0],
                        "cities": conn.execute("SELECT COUNT(DISTINCT city) FROM Vacancies").fetchone()[0],
                    }
                self.send_json(stats)
            except Exception as error:
                self.send_json({"error": str(error)}, 500)
            return
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != "/api/applications":
            self.send_json({"error": "Мұндай API мекенжайы жоқ."}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            result = submit_application(data)
            self.send_json(result)
        except ValueError as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
        except Exception as error:
            self.send_json({"ok": False, "error": str(error)}, 500)


def main():
    WEB_DIR.mkdir(exist_ok=True)
    # Қосымша іске қосылғанда тексеру жасалады. CREATE TABLE немесе DROP TABLE жоқ.
    with connect_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM Vacancies").fetchone()[0]
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"Деректер базасы қосылды. Вакансия саны: {count}")
    print(f"Сайт ашылды: {url}")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер тоқтатылды.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
