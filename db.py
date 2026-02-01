import sqlite3
import hashlib
import json
import os
from pathlib import Path

DB_DIR = Path(__file__).parent / "data"
DB_PATH = DB_DIR / "users.db"
TESTS_DIR = Path(__file__).parent / "tests"


def get_connection():
    DB_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS test_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            test_file TEXT NOT NULL,
            score INTEGER NOT NULL,
            total INTEGER NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS question_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            test_file TEXT NOT NULL,
            question_id INTEGER NOT NULL,
            correct BOOLEAN NOT NULL,
            session_id INTEGER,
            answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (session_id) REFERENCES test_sessions(id)
        );
        CREATE TABLE IF NOT EXISTS favorite_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            test_file TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, test_file)
        );
        CREATE TABLE IF NOT EXISTS tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            author TEXT DEFAULT '',
            source_file TEXT,
            is_public BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (owner_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            question_num INTEGER NOT NULL,
            tag TEXT NOT NULL,
            question TEXT NOT NULL,
            options TEXT NOT NULL,
            answer_index INTEGER NOT NULL,
            explanation TEXT DEFAULT '',
            FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS test_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            material_type TEXT NOT NULL,
            title TEXT DEFAULT '',
            url TEXT DEFAULT '',
            file_data BLOB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS question_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            context TEXT DEFAULT '',
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
            FOREIGN KEY (material_id) REFERENCES test_materials(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS programs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (owner_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS program_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            program_id INTEGER NOT NULL,
            test_id INTEGER NOT NULL,
            FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE,
            FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE,
            UNIQUE(program_id, test_id)
        );
        CREATE TABLE IF NOT EXISTS program_collaborators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            program_id INTEGER NOT NULL,
            user_email TEXT NOT NULL,
            user_id INTEGER,
            role TEXT NOT NULL CHECK(role IN ('student','guest','reviewer','admin')),
            invited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE,
            UNIQUE(program_id, user_email)
        );
        CREATE TABLE IF NOT EXISTS test_collaborators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            user_email TEXT NOT NULL,
            user_id INTEGER,
            role TEXT NOT NULL CHECK(role IN ('student','guest','reviewer','admin')),
            invited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE,
            UNIQUE(test_id, user_email)
        );
    """)
    # Migrations for older DB versions
    cursor = conn.execute("PRAGMA table_info(question_history)")
    columns = [row[1] for row in cursor.fetchall()]
    if "session_id" not in columns:
        conn.execute("ALTER TABLE question_history ADD COLUMN session_id INTEGER REFERENCES test_sessions(id)")
        conn.commit()

    cursor = conn.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in cursor.fetchall()]
    if "display_name" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
        conn.commit()
    if "avatar" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN avatar BLOB")
        conn.commit()

    # Add pause_times and questions_per_pause to test_materials
    cursor = conn.execute("PRAGMA table_info(test_materials)")
    mat_cols = [row[1] for row in cursor.fetchall()]
    if "pause_times" not in mat_cols:
        conn.execute("ALTER TABLE test_materials ADD COLUMN pause_times TEXT DEFAULT ''")
        conn.commit()
    if "questions_per_pause" not in mat_cols:
        conn.execute("ALTER TABLE test_materials ADD COLUMN questions_per_pause INTEGER DEFAULT 1")
        conn.commit()
    if "transcript" not in mat_cols:
        conn.execute("ALTER TABLE test_materials ADD COLUMN transcript TEXT DEFAULT ''")
        conn.commit()

    # Add test_id columns to existing tables for migration
    for table in ["test_sessions", "question_history", "favorite_tests"]:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        cols = [row[1] for row in cursor.fetchall()]
        if "test_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN test_id INTEGER REFERENCES tests(id)")
            conn.commit()

    # Add source column to questions
    cursor = conn.execute("PRAGMA table_info(questions)")
    cols = [row[1] for row in cursor.fetchall()]
    if "source" not in cols:
        conn.execute("ALTER TABLE questions ADD COLUMN source TEXT DEFAULT 'manual'")
        conn.commit()

    # Add language column to tests
    cursor = conn.execute("PRAGMA table_info(tests)")
    cols = [row[1] for row in cursor.fetchall()]
    if "language" not in cols:
        conn.execute("ALTER TABLE tests ADD COLUMN language TEXT DEFAULT ''")
        conn.commit()

    # Migrate test_collaborators to add 'student' role to CHECK constraint
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='test_collaborators'").fetchone()
    if row and "'student'" not in row[0]:
        conn.executescript("""
            CREATE TABLE test_collaborators_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id INTEGER NOT NULL,
                user_email TEXT NOT NULL,
                user_id INTEGER,
                role TEXT NOT NULL CHECK(role IN ('student','guest','reviewer','admin')),
                invited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE,
                UNIQUE(test_id, user_email)
            );
            INSERT INTO test_collaborators_new SELECT * FROM test_collaborators;
            DROP TABLE test_collaborators;
            ALTER TABLE test_collaborators_new RENAME TO test_collaborators;
        """)

    # Add visibility column to tests (public / private / hidden)
    if "visibility" not in cols:
        conn.execute("ALTER TABLE tests ADD COLUMN visibility TEXT DEFAULT 'public'")
        conn.execute("UPDATE tests SET visibility = CASE WHEN is_public = 1 THEN 'public' ELSE 'hidden' END WHERE visibility IS NULL OR visibility = 'public'")
        conn.commit()

    # Add visibility column to programs
    cursor = conn.execute("PRAGMA table_info(programs)")
    prog_cols = [row[1] for row in cursor.fetchall()]
    if "visibility" not in prog_cols:
        conn.execute("ALTER TABLE programs ADD COLUMN visibility TEXT DEFAULT 'public'")
        conn.commit()

    conn.close()

    # Import JSON tests and backfill references
    auto_import_json_tests()


# --- JSON import ---

def auto_import_json_tests():
    """Import JSON test files from disk into the DB if not already imported."""
    if not TESTS_DIR.exists():
        return
    conn = get_connection()
    for file in TESTS_DIR.glob("*.json"):
        # Check if already imported by source_file
        row = conn.execute(
            "SELECT id FROM tests WHERE source_file = ?", (file.stem,)
        ).fetchone()
        if row:
            test_id = row[0]
        else:
            test_id = _import_json_file(conn, file)

        # Backfill test_id in old tables where test_file matches this stem
        if test_id:
            for table in ["test_sessions", "question_history", "favorite_tests"]:
                conn.execute(
                    f"UPDATE {table} SET test_id = ? WHERE test_file = ? AND test_id IS NULL",
                    (test_id, file.stem),
                )
            conn.commit()
    conn.close()


def _import_json_file(conn, file_path):
    """Import a single JSON test file into the DB. Returns test_id."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        title = file_path.stem.replace("_", " ").title()
        description = ""
        author = ""
        questions_data = data
    else:
        title = data.get("title") or file_path.stem.replace("_", " ").title()
        description = data.get("description", "")
        author = data.get("author", "")
        questions_data = data.get("questions", [])

    language = data.get("language", "") if isinstance(data, dict) else ""
    visibility = data.get("visibility", "public") if isinstance(data, dict) else "public"

    cursor = conn.execute(
        "INSERT INTO tests (owner_id, title, description, author, source_file, is_public, language, visibility) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
        (None, title, description, author, file_path.stem, language, visibility),
    )
    test_id = cursor.lastrowid

    # Import materials
    mat_id_map = {}
    materials_data = data.get("materials", []) if isinstance(data, dict) else []
    for mat in materials_data:
        old_id = mat.get("id")
        mat_cursor = conn.execute(
            "INSERT INTO test_materials (test_id, material_type, title, url, pause_times, transcript) VALUES (?, ?, ?, ?, ?, ?)",
            (test_id, mat.get("material_type", "url"), mat.get("title", ""), mat.get("url", ""),
             mat.get("pause_times", ""), mat.get("transcript", "")),
        )
        if old_id is not None:
            mat_id_map[old_id] = mat_cursor.lastrowid

    # Import collaborators
    collabs_data = data.get("collaborators", []) if isinstance(data, dict) else []
    for collab in collabs_data:
        email = collab.get("email", "").strip()
        role = collab.get("role", "guest")
        if email and role in ("student", "guest", "reviewer", "admin"):
            conn.execute(
                "INSERT OR IGNORE INTO test_collaborators (test_id, user_email, role) VALUES (?, ?, ?)",
                (test_id, email, role),
            )

    for q in questions_data:
        q_cursor = conn.execute(
            "INSERT INTO questions (test_id, question_num, tag, question, options, answer_index, explanation) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (test_id, q["id"], q["tag"], q["question"], json.dumps(q["options"], ensure_ascii=False), q["answer_index"], q.get("explanation", "")),
        )
        # Import material references
        for ref in q.get("material_refs", []):
            new_mid = mat_id_map.get(ref.get("material_id"))
            if new_mid:
                conn.execute(
                    "INSERT INTO question_materials (question_id, material_id, context) VALUES (?, ?, ?)",
                    (q_cursor.lastrowid, new_mid, ref.get("context", "")),
                )

    conn.commit()
    return test_id


# --- Test CRUD ---

def create_test(owner_id, title, description="", author="", language=""):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO tests (owner_id, title, description, author, is_public, language) VALUES (?, ?, ?, ?, 1, ?)",
        (owner_id, title, description, author, language),
    )
    test_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return test_id


def update_test(test_id, title, description="", author="", language="", visibility="public"):
    conn = get_connection()
    conn.execute(
        "UPDATE tests SET title = ?, description = ?, author = ?, language = ?, visibility = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (title, description, author, language, visibility, test_id),
    )
    conn.commit()
    conn.close()


def delete_test(test_id):
    conn = get_connection()
    conn.execute("DELETE FROM tests WHERE id = ?", (test_id,))
    conn.commit()
    conn.close()


def get_test(test_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, owner_id, title, description, author, is_public, created_at, updated_at, language, visibility FROM tests WHERE id = ?",
        (test_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "id": row[0], "owner_id": row[1], "title": row[2],
        "description": row[3], "author": row[4], "is_public": row[5],
        "created_at": row[6], "updated_at": row[7], "language": row[8] or "",
        "visibility": row[9] or "public",
    }


def get_all_tests(user_id=None):
    """Return public + private tests (visible to all), plus owner's hidden tests."""
    conn = get_connection()
    if user_id:
        rows = conn.execute(
            """SELECT id, owner_id, title, description, author, is_public,
                      (SELECT COUNT(*) FROM questions WHERE questions.test_id = tests.id) as q_count,
                      language, visibility
               FROM tests
               WHERE visibility IN ('public', 'private') OR owner_id = ?
               ORDER BY title""",
            (user_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, owner_id, title, description, author, is_public,
                      (SELECT COUNT(*) FROM questions WHERE questions.test_id = tests.id) as q_count,
                      language, visibility
               FROM tests
               WHERE visibility IN ('public', 'private')
               ORDER BY title""",
        ).fetchall()
    conn.close()
    return [
        {"id": r[0], "owner_id": r[1], "title": r[2], "description": r[3],
         "author": r[4], "is_public": r[5], "question_count": r[6], "language": r[7] or "",
         "visibility": r[8] or "public"}
        for r in rows
    ]


# --- Question CRUD ---

def get_test_questions(test_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, question_num, tag, question, options, answer_index, explanation, source
           FROM questions WHERE test_id = ?
           ORDER BY question_num""",
        (test_id,),
    ).fetchall()
    conn.close()
    return [
        {"id": r[1], "tag": r[2], "question": r[3],
         "options": json.loads(r[4]), "answer_index": r[5],
         "explanation": r[6], "db_id": r[0], "source": r[7] or "manual"}
        for r in rows
    ]


def get_test_questions_by_ids(test_id, question_nums):
    """Get specific questions by their question_num within a test."""
    if not question_nums:
        return []
    conn = get_connection()
    placeholders = ",".join("?" for _ in question_nums)
    rows = conn.execute(
        f"""SELECT id, question_num, tag, question, options, answer_index, explanation, source
            FROM questions WHERE test_id = ? AND question_num IN ({placeholders})
            ORDER BY question_num""",
        (test_id, *question_nums),
    ).fetchall()
    conn.close()
    return [
        {"id": r[1], "tag": r[2], "question": r[3],
         "options": json.loads(r[4]), "answer_index": r[5],
         "explanation": r[6], "db_id": r[0], "source": r[7] or "manual"}
        for r in rows
    ]


def add_question(test_id, question_num, tag, question, options, answer_index, explanation="", source="manual"):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO questions (test_id, question_num, tag, question, options, answer_index, explanation, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (test_id, question_num, tag, question, json.dumps(options, ensure_ascii=False), answer_index, explanation, source),
    )
    q_id = cursor.lastrowid
    conn.execute("UPDATE tests SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (test_id,))
    conn.commit()
    conn.close()
    return q_id


def get_next_question_num(test_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(MAX(question_num), 0) + 1 FROM questions WHERE test_id = ?",
        (test_id,),
    ).fetchone()
    conn.close()
    return row[0]


def update_question(db_id, tag, question, options, answer_index, explanation=""):
    conn = get_connection()
    conn.execute(
        "UPDATE questions SET tag = ?, question = ?, options = ?, answer_index = ?, explanation = ? WHERE id = ?",
        (tag, question, json.dumps(options, ensure_ascii=False), answer_index, explanation, db_id),
    )
    # Update test timestamp
    conn.execute(
        "UPDATE tests SET updated_at = CURRENT_TIMESTAMP WHERE id = (SELECT test_id FROM questions WHERE id = ?)",
        (db_id,),
    )
    conn.commit()
    conn.close()


def delete_question(db_id):
    conn = get_connection()
    conn.execute("DELETE FROM questions WHERE id = ?", (db_id,))
    conn.commit()
    conn.close()


def get_test_tags(test_id):
    """Get unique tags for a test."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT tag FROM questions WHERE test_id = ? ORDER BY tag",
        (test_id,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def rename_test_tag(test_id, old_tag, new_tag):
    conn = get_connection()
    conn.execute(
        "UPDATE questions SET tag = ? WHERE test_id = ? AND tag = ?",
        (new_tag, test_id, old_tag),
    )
    conn.execute("UPDATE tests SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (test_id,))
    conn.commit()
    conn.close()


def delete_test_tag(test_id, tag, delete_questions=False):
    conn = get_connection()
    if delete_questions:
        conn.execute("DELETE FROM questions WHERE test_id = ? AND tag = ?", (test_id, tag))
    else:
        conn.execute("UPDATE questions SET tag = '' WHERE test_id = ? AND tag = ?", (test_id, tag))
    conn.execute("UPDATE tests SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (test_id,))
    conn.commit()
    conn.close()


# --- Materials ---

def get_test_materials(test_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, material_type, title, url, file_data, created_at, pause_times, questions_per_pause, transcript FROM test_materials WHERE test_id = ? ORDER BY created_at",
        (test_id,),
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "material_type": r[1], "title": r[2], "url": r[3], "file_data": r[4], "created_at": r[5],
         "pause_times": r[6] or "", "questions_per_pause": r[7] or 1, "transcript": r[8] or ""}
        for r in rows
    ]


def add_test_material(test_id, material_type, title, url="", file_data=None, pause_times="", questions_per_pause=1, transcript=""):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO test_materials (test_id, material_type, title, url, file_data, pause_times, questions_per_pause, transcript) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (test_id, material_type, title, url, file_data, pause_times, questions_per_pause, transcript),
    )
    mat_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return mat_id


def update_test_material(material_id, title, url="", pause_times="", questions_per_pause=1):
    conn = get_connection()
    conn.execute(
        "UPDATE test_materials SET title = ?, url = ?, pause_times = ?, questions_per_pause = ? WHERE id = ?",
        (title, url, pause_times, questions_per_pause, material_id),
    )
    conn.commit()
    conn.close()


def delete_test_material(material_id):
    conn = get_connection()
    conn.execute("DELETE FROM test_materials WHERE id = ?", (material_id,))
    conn.commit()
    conn.close()


def update_material_transcript(material_id, transcript):
    conn = get_connection()
    conn.execute("UPDATE test_materials SET transcript = ? WHERE id = ?", (transcript, material_id))
    conn.commit()
    conn.close()


# --- Question-Material Links ---

def get_question_material_links(question_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT material_id, context FROM question_materials WHERE question_id = ?",
        (question_id,),
    ).fetchall()
    conn.close()
    return [{"material_id": r[0], "context": r[1] or ""} for r in rows]


def get_question_material_links_bulk(question_ids):
    """Return {question_id: [{material_id, context}]} for multiple questions."""
    if not question_ids:
        return {}
    conn = get_connection()
    placeholders = ",".join("?" for _ in question_ids)
    rows = conn.execute(
        f"SELECT question_id, material_id, context FROM question_materials WHERE question_id IN ({placeholders})",
        tuple(question_ids),
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault(r[0], []).append({"material_id": r[1], "context": r[2] or ""})
    return result


def set_question_material_links(question_id, links):
    """Replace all material links for a question. links = [{material_id, context}]."""
    conn = get_connection()
    conn.execute("DELETE FROM question_materials WHERE question_id = ?", (question_id,))
    for link in links:
        conn.execute(
            "INSERT INTO question_materials (question_id, material_id, context) VALUES (?, ?, ?)",
            (question_id, link["material_id"], link.get("context", "")),
        )
    conn.commit()
    conn.close()


# --- Programs ---

def create_program(owner_id, title, description=""):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO programs (owner_id, title, description) VALUES (?, ?, ?)",
        (owner_id, title, description),
    )
    program_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return program_id


def update_program(program_id, title, description="", visibility="public"):
    conn = get_connection()
    conn.execute(
        "UPDATE programs SET title = ?, description = ?, visibility = ? WHERE id = ?",
        (title, description, visibility, program_id),
    )
    conn.commit()
    conn.close()


def delete_program(program_id):
    conn = get_connection()
    conn.execute("DELETE FROM programs WHERE id = ?", (program_id,))
    conn.commit()
    conn.close()


def get_program(program_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, owner_id, title, description, created_at, visibility FROM programs WHERE id = ?",
        (program_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {"id": row[0], "owner_id": row[1], "title": row[2], "description": row[3], "created_at": row[4], "visibility": row[5] or "public"}


def get_all_programs(user_id):
    """Return public/private programs + user's own programs."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT p.id, p.owner_id, p.title, p.description, p.created_at,
                  (SELECT COUNT(*) FROM program_tests WHERE program_id = p.id) as test_count,
                  p.visibility
           FROM programs p
           WHERE p.visibility IN ('public', 'private') OR p.owner_id = ?
           ORDER BY p.title""",
        (user_id,),
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "owner_id": r[1], "title": r[2], "description": r[3],
         "created_at": r[4], "test_count": r[5], "visibility": r[6] or "public"}
        for r in rows
    ]


def add_test_to_program(program_id, test_id):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO program_tests (program_id, test_id) VALUES (?, ?)",
            (program_id, test_id),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()


def remove_test_from_program(program_id, test_id):
    conn = get_connection()
    conn.execute(
        "DELETE FROM program_tests WHERE program_id = ? AND test_id = ?",
        (program_id, test_id),
    )
    conn.commit()
    conn.close()


def get_program_tests(program_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT t.id, t.title, t.description, t.author,
                  (SELECT COUNT(*) FROM questions WHERE test_id = t.id) as q_count
           FROM tests t
           JOIN program_tests pt ON pt.test_id = t.id
           WHERE pt.program_id = ?
           ORDER BY t.title""",
        (program_id,),
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "title": r[1], "description": r[2], "author": r[3], "question_count": r[4]}
        for r in rows
    ]


def get_program_questions(program_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT q.id, q.question_num, q.tag, q.question, q.options, q.answer_index, q.explanation, q.source
           FROM questions q
           JOIN program_tests pt ON pt.test_id = q.test_id
           WHERE pt.program_id = ?
           ORDER BY q.test_id, q.question_num""",
        (program_id,),
    ).fetchall()
    conn.close()
    return [
        {"id": r[1], "tag": r[2], "question": r[3],
         "options": json.loads(r[4]), "answer_index": r[5],
         "explanation": r[6], "db_id": r[0], "source": r[7] or "manual"}
        for r in rows
    ]


def get_program_tags(program_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT DISTINCT q.tag FROM questions q
           JOIN program_tests pt ON pt.test_id = q.test_id
           WHERE pt.program_id = ?
           ORDER BY q.tag""",
        (program_id,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


# --- User auth ---

def _hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return hashed, salt


def create_user(username, password):
    hashed, salt = _hash_password(password)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)",
            (username, hashed, salt),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def authenticate(username, password):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, password_hash, salt FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    user_id, stored_hash, salt = row
    hashed, _ = _hash_password(password, salt)
    if hashed == stored_hash:
        return user_id
    return None


def get_or_create_google_user(email, name):
    """Get or create a user account for Google OAuth login."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        (email,),
    ).fetchone()
    if row:
        conn.close()
        return row[0]
    try:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)",
            (email, "oauth_google", "oauth"),
        )
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return user_id
    except sqlite3.IntegrityError:
        conn.close()
        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (email,),
        ).fetchone()
        conn.close()
        return row[0] if row else None


# --- Sessions and history ---

def create_session(user_id, test_id, score, total):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO test_sessions (user_id, test_file, test_id, score, total) VALUES (?, ?, ?, ?, ?)",
        (user_id, str(test_id) if test_id else "", test_id, score, total),
    )
    session_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return session_id


def update_session_score(session_id, score, total):
    conn = get_connection()
    conn.execute(
        "UPDATE test_sessions SET score = ?, total = ? WHERE id = ?",
        (score, total, session_id),
    )
    conn.commit()
    conn.close()


def record_answer(user_id, test_id, question_id, correct, session_id=None):
    conn = get_connection()
    conn.execute(
        "INSERT INTO question_history (user_id, test_file, test_id, question_id, correct, session_id) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, str(test_id) if test_id else "", test_id if test_id else None, question_id, correct, session_id),
    )
    conn.commit()
    conn.close()


def get_question_stats(user_id, test_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT question_id,
                  SUM(CASE WHEN correct THEN 1 ELSE 0 END) as correct_count,
                  SUM(CASE WHEN NOT correct THEN 1 ELSE 0 END) as wrong_count
           FROM question_history
           WHERE user_id = ? AND test_id = ?
           GROUP BY question_id""",
        (user_id, test_id),
    ).fetchall()
    conn.close()
    return {row[0]: {"correct": row[1], "wrong": row[2]} for row in rows}


def get_user_sessions(user_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT ts.id, ts.test_id, ts.score, ts.total, ts.started_at,
                  COALESCE(t.title, ts.test_file) as title
           FROM test_sessions ts
           LEFT JOIN tests t ON ts.test_id = t.id
           WHERE ts.user_id = ?
           ORDER BY ts.started_at DESC""",
        (user_id,),
    ).fetchall()
    conn.close()
    return [{"id": r[0], "test_id": r[1], "score": r[2], "total": r[3], "date": r[4], "title": r[5]} for r in rows]


def get_session_wrong_answers(session_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT question_id, test_id
           FROM question_history
           WHERE session_id = ? AND NOT correct""",
        (session_id,),
    ).fetchall()
    conn.close()
    return [{"question_id": r[0], "test_id": r[1]} for r in rows]


def get_all_wrong_question_ids(user_id, test_id=None):
    """Get question_ids where user has more wrong than correct answers."""
    conn = get_connection()
    if test_id:
        rows = conn.execute(
            """SELECT question_id, test_id,
                      SUM(CASE WHEN correct THEN 1 ELSE 0 END) as c,
                      SUM(CASE WHEN NOT correct THEN 1 ELSE 0 END) as w
               FROM question_history
               WHERE user_id = ? AND test_id = ?
               GROUP BY question_id, test_id
               HAVING w > c""",
            (user_id, test_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT question_id, test_id,
                      SUM(CASE WHEN correct THEN 1 ELSE 0 END) as c,
                      SUM(CASE WHEN NOT correct THEN 1 ELSE 0 END) as w
               FROM question_history
               WHERE user_id = ?
               GROUP BY question_id, test_id
               HAVING w > c""",
            (user_id,),
        ).fetchall()
    conn.close()
    return [{"question_id": r[0], "test_id": r[1], "correct": r[2], "wrong": r[3]} for r in rows]


# --- Profile ---

def get_user_profile(user_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT display_name, avatar FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return {"display_name": None, "avatar": None}
    return {"display_name": row[0], "avatar": row[1]}


def update_user_profile(user_id, display_name=None, avatar_bytes=None):
    conn = get_connection()
    if avatar_bytes is not None:
        conn.execute(
            "UPDATE users SET display_name = ?, avatar = ? WHERE id = ?",
            (display_name, avatar_bytes, user_id),
        )
    else:
        conn.execute(
            "UPDATE users SET display_name = ? WHERE id = ?",
            (display_name, user_id),
        )
    conn.commit()
    conn.close()


# --- Favorites ---

def toggle_favorite(user_id, test_id):
    """Toggle a test as favorite. Returns True if now favorited, False if removed."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM favorite_tests WHERE user_id = ? AND test_id = ?",
        (user_id, test_id),
    ).fetchone()
    if row:
        conn.execute("DELETE FROM favorite_tests WHERE id = ?", (row[0],))
        conn.commit()
        conn.close()
        return False
    else:
        conn.execute(
            "INSERT INTO favorite_tests (user_id, test_file, test_id) VALUES (?, ?, ?)",
            (user_id, str(test_id), test_id),
        )
        conn.commit()
        conn.close()
        return True


# --- Collaborators ---

def add_collaborator(test_id, email, role):
    """Add or update a collaborator for a test."""
    conn = get_connection()
    # Try to resolve user_id from email
    row = conn.execute("SELECT id FROM users WHERE username = ?", (email,)).fetchone()
    uid = row[0] if row else None
    try:
        conn.execute(
            "INSERT INTO test_collaborators (test_id, user_email, user_id, role) VALUES (?, ?, ?, ?)",
            (test_id, email, uid, role),
        )
    except sqlite3.IntegrityError:
        conn.execute(
            "UPDATE test_collaborators SET role = ?, user_id = COALESCE(?, user_id) WHERE test_id = ? AND user_email = ?",
            (role, uid, test_id, email),
        )
    conn.commit()
    conn.close()


def remove_collaborator(test_id, email):
    conn = get_connection()
    conn.execute("DELETE FROM test_collaborators WHERE test_id = ? AND user_email = ?", (test_id, email))
    conn.commit()
    conn.close()


def update_collaborator_role(test_id, email, new_role):
    conn = get_connection()
    conn.execute(
        "UPDATE test_collaborators SET role = ? WHERE test_id = ? AND user_email = ?",
        (new_role, test_id, email),
    )
    conn.commit()
    conn.close()


def get_collaborators(test_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, user_email, user_id, role, invited_at FROM test_collaborators WHERE test_id = ? ORDER BY invited_at",
        (test_id,),
    ).fetchall()
    conn.close()
    return [{"id": r[0], "email": r[1], "user_id": r[2], "role": r[3], "invited_at": r[4]} for r in rows]


def get_user_role_for_test(test_id, user_id):
    """Return the collaboration role for a user on a test, or None.
    Checks direct test collaborators first, then program-level collaborators."""
    conn = get_connection()
    # Direct test collaborator
    row = conn.execute(
        """SELECT tc.role FROM test_collaborators tc
           LEFT JOIN users u ON u.username = tc.user_email
           WHERE tc.test_id = ? AND (tc.user_id = ? OR u.id = ?)
           LIMIT 1""",
        (test_id, user_id, user_id),
    ).fetchone()
    if row:
        conn.close()
        return row[0]
    # Program-level collaborator (test belongs to a program the user collaborates on)
    row = conn.execute(
        """SELECT pc.role FROM program_collaborators pc
           JOIN program_tests pt ON pt.program_id = pc.program_id
           LEFT JOIN users u ON u.username = pc.user_email
           WHERE pt.test_id = ? AND (pc.user_id = ? OR u.id = ?)
           LIMIT 1""",
        (test_id, user_id, user_id),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def get_shared_tests(user_id):
    """Return tests shared with a user (as collaborator)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT t.id, t.owner_id, t.title, t.description, t.author, t.is_public,
                  (SELECT COUNT(*) FROM questions WHERE questions.test_id = t.id) as q_count,
                  t.language, tc.role, t.visibility
           FROM tests t
           JOIN test_collaborators tc ON tc.test_id = t.id
           LEFT JOIN users u ON u.username = tc.user_email
           WHERE tc.user_id = ? OR u.id = ?
           ORDER BY t.title""",
        (user_id, user_id),
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "owner_id": r[1], "title": r[2], "description": r[3],
         "author": r[4], "is_public": r[5], "question_count": r[6], "language": r[7] or "", "role": r[8],
         "visibility": r[9] or "public"}
        for r in rows
    ]


def resolve_collaborator_user_id(email, user_id):
    """Fill in user_id for collaborator entries matching this email."""
    conn = get_connection()
    conn.execute(
        "UPDATE test_collaborators SET user_id = ? WHERE user_email = ? AND user_id IS NULL",
        (user_id, email),
    )
    conn.execute(
        "UPDATE program_collaborators SET user_id = ? WHERE user_email = ? AND user_id IS NULL",
        (user_id, email),
    )
    conn.commit()
    conn.close()


# --- Program Collaborators ---

def add_program_collaborator(program_id, email, role):
    conn = get_connection()
    row = conn.execute("SELECT id FROM users WHERE username = ?", (email,)).fetchone()
    uid = row[0] if row else None
    try:
        conn.execute(
            "INSERT INTO program_collaborators (program_id, user_email, user_id, role) VALUES (?, ?, ?, ?)",
            (program_id, email, uid, role),
        )
    except sqlite3.IntegrityError:
        conn.execute(
            "UPDATE program_collaborators SET role = ?, user_id = COALESCE(?, user_id) WHERE program_id = ? AND user_email = ?",
            (role, uid, program_id, email),
        )
    conn.commit()
    conn.close()


def remove_program_collaborator(program_id, email):
    conn = get_connection()
    conn.execute("DELETE FROM program_collaborators WHERE program_id = ? AND user_email = ?", (program_id, email))
    conn.commit()
    conn.close()


def update_program_collaborator_role(program_id, email, new_role):
    conn = get_connection()
    conn.execute(
        "UPDATE program_collaborators SET role = ? WHERE program_id = ? AND user_email = ?",
        (new_role, program_id, email),
    )
    conn.commit()
    conn.close()


def get_program_collaborators(program_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, user_email, user_id, role, invited_at FROM program_collaborators WHERE program_id = ? ORDER BY invited_at",
        (program_id,),
    ).fetchall()
    conn.close()
    return [{"id": r[0], "email": r[1], "user_id": r[2], "role": r[3], "invited_at": r[4]} for r in rows]


def get_user_role_for_program(program_id, user_id):
    """Return the collaboration role for a user on a program, or None."""
    conn = get_connection()
    row = conn.execute(
        """SELECT pc.role FROM program_collaborators pc
           LEFT JOIN users u ON u.username = pc.user_email
           WHERE pc.program_id = ? AND (pc.user_id = ? OR u.id = ?)
           LIMIT 1""",
        (program_id, user_id, user_id),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def get_shared_programs(user_id):
    """Return programs shared with a user (as collaborator)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT p.id, p.owner_id, p.title, p.description, p.created_at,
                  (SELECT COUNT(*) FROM program_tests WHERE program_id = p.id) as test_count,
                  p.visibility, pc.role
           FROM programs p
           JOIN program_collaborators pc ON pc.program_id = p.id
           LEFT JOIN users u ON u.username = pc.user_email
           WHERE pc.user_id = ? OR u.id = ?
           ORDER BY p.title""",
        (user_id, user_id),
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "owner_id": r[1], "title": r[2], "description": r[3],
         "created_at": r[4], "test_count": r[5], "visibility": r[6] or "public", "role": r[7]}
        for r in rows
    ]


def get_favorite_tests(user_id):
    """Return set of test_ids that are favorited by the user."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT test_id FROM favorite_tests WHERE user_id = ? AND test_id IS NOT NULL",
        (user_id,),
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}
