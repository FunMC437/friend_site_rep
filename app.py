from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path
from typing import Iterable

from flask import Flask, flash, g, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
DATA_ROOT = Path(os.getenv("DATA_ROOT", str(BASE_DIR)))
DATABASE_PATH = DATA_ROOT / "app.db"
UPLOADS_DIR = DATA_ROOT / "uploads"

AUTHOR_EMAIL = os.getenv("AUTHOR_EMAIL", "tom@gmail.com")
AUTHOR_PASSWORD = os.getenv("AUTHOR_PASSWORD", "12344321")

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
ALLOWED_VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "m4v"}
BAD_WORDS = (
    "хуй",
    "пизд",
    "еба",
    "ебл",
    "бля",
    "сука",
    "нахуй",
    "мудак",
    "fuck",
    "shit",
)
REACTIONS = {
    "like": {"column": "like_count"},
    "heart": {"column": "heart_count"},
    "fire": {"column": "fire_count"},
}


app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "taste-stream-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_: object | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def ensure_column(cursor: sqlite3.Cursor, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def is_allowed_file(filename: str, allowed_extensions: set[str]) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_extensions


def save_uploaded_file(file_storage, allowed_extensions: set[str]) -> str:
    filename = secure_filename(file_storage.filename or "")
    if not filename or not is_allowed_file(filename, allowed_extensions):
        raise ValueError("Неподдерживаемый формат файла.")

    extension = filename.rsplit(".", 1)[1].lower()
    new_name = f"{uuid.uuid4().hex}.{extension}"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    destination = UPLOADS_DIR / new_name
    file_storage.save(destination)
    return f"uploads/{new_name}"


def normalize_text(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum() or ch.isspace())


def contains_bad_words(text: str) -> bool:
    normalized = normalize_text(text)
    return any(word in normalized for word in BAD_WORDS)


def init_db() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(DATABASE_PATH)
    db.row_factory = sqlite3.Row
    cursor = db.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS author (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            email TEXT NOT NULL,
            password_hash TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            image_url TEXT,
            video_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            author_name TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
        """
    )

    ensure_column(cursor, "posts", "like_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(cursor, "posts", "heart_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(cursor, "posts", "fire_count", "INTEGER NOT NULL DEFAULT 0")

    cursor.execute("SELECT id FROM author WHERE id = 1")
    if cursor.fetchone() is None:
        cursor.execute(
            "INSERT INTO author (id, email, password_hash) VALUES (1, ?, ?)",
            (AUTHOR_EMAIL, generate_password_hash(AUTHOR_PASSWORD)),
        )
    else:
        cursor.execute(
            "UPDATE author SET email = ?, password_hash = ? WHERE id = 1",
            (AUTHOR_EMAIL, generate_password_hash(AUTHOR_PASSWORD)),
        )

    cursor.execute("SELECT COUNT(*) AS count FROM posts")
    if cursor.fetchone()["count"] == 0:
        seed_posts: Iterable[tuple[str, str, str, str, str]] = [
            (
                "Теплая фокачча с травами",
                "recipe",
                "Мягкая домашняя фокачча с оливковым маслом, чесноком и ароматными травами. Идеальна к супам, пасте и просто к вечернему чаю.",
                "https://images.unsplash.com/photo-1546549032-9571cd6b27df?auto=format&fit=crop&w=1400&q=80",
                "",
            ),
            (
                "Видео: карамельные сырники без лишней возни",
                "video",
                "Быстрый ролик с мягкими сырниками, золотистой корочкой и аккуратной подачей. Подойдет для завтрака или уютного brunch.",
                "https://images.unsplash.com/photo-1517673400267-0251440c45dc?auto=format&fit=crop&w=1400&q=80",
                "https://www.w3schools.com/html/mov_bbb.mp4",
            ),
        ]
        cursor.executemany(
            """
            INSERT INTO posts (title, category, description, image_url, video_url)
            VALUES (?, ?, ?, ?, ?)
            """,
            seed_posts,
        )

    db.commit()
    db.close()


def map_post_row(post: sqlite3.Row, comments_by_post: dict[int, list[sqlite3.Row]]) -> dict:
    video_url = post["video_url"] or ""
    image_url = post["image_url"] or "https://images.unsplash.com/photo-1504674900247-0877df9cc836?auto=format&fit=crop&w=1400&q=80"
    if video_url.startswith("uploads/"):
        video_url = url_for("uploaded_file", filename=video_url.removeprefix("uploads/"))
    if image_url.startswith("uploads/"):
        image_url = url_for("uploaded_file", filename=image_url.removeprefix("uploads/"))

    return {
        "id": post["id"],
        "title": post["title"],
        "category": post["category"],
        "description": post["description"],
        "image_url": image_url,
        "video_url": video_url,
        "is_uploaded_video": post["video_url"].startswith("uploads/") if post["video_url"] else False,
        "created_at": post["created_at"],
        "like_count": post["like_count"],
        "heart_count": post["heart_count"],
        "fire_count": post["fire_count"],
        "comments": comments_by_post.get(post["id"], []),
    }


def fetch_posts(category: str | None = None) -> list[dict]:
    db = get_db()
    query = """
        SELECT id, title, category, description, image_url, video_url, created_at,
               like_count, heart_count, fire_count
        FROM posts
    """
    params: list[str] = []
    if category:
        query += " WHERE category = ?"
        params.append(category)
    query += " ORDER BY datetime(created_at) DESC, id DESC"

    posts_rows = db.execute(query, params).fetchall()
    comments_rows = db.execute(
        """
        SELECT id, post_id, author_name, body, created_at
        FROM comments
        ORDER BY datetime(created_at) DESC, id DESC
        """
    ).fetchall()

    comments_by_post: dict[int, list[sqlite3.Row]] = {}
    for comment in comments_rows:
        comments_by_post.setdefault(comment["post_id"], []).append(comment)

    return [map_post_row(post, comments_by_post) for post in posts_rows]


def fetch_latest_posts(limit: int = 3) -> list[dict]:
    db = get_db()
    posts_rows = db.execute(
        """
        SELECT id, title, category, description, image_url, video_url, created_at,
               like_count, heart_count, fire_count
        FROM posts
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    comments_rows = db.execute(
        """
        SELECT id, post_id, author_name, body, created_at
        FROM comments
        ORDER BY datetime(created_at) DESC, id DESC
        """
    ).fetchall()
    comments_by_post: dict[int, list[sqlite3.Row]] = {}
    for comment in comments_rows:
        comments_by_post.setdefault(comment["post_id"], []).append(comment)
    return [map_post_row(post, comments_by_post) for post in posts_rows]


def is_author_logged_in() -> bool:
    return bool(session.get("author_logged_in"))


@app.route("/uploads/<path:filename>")
def uploaded_file(filename: str):
    return send_from_directory(UPLOADS_DIR, filename)


@app.context_processor
def inject_globals():
    register_mode = request.args.get("mode", "").strip() == "register"
    return {
        "is_author_logged_in": is_author_logged_in(),
        "current_email": AUTHOR_EMAIL,
        "register_mode": register_mode,
    }


@app.route("/")
def home():
    return render_template("home.html", posts=fetch_latest_posts())


@app.route("/recipes")
def recipes():
    return render_template("recipes.html", posts=fetch_posts("recipe"))


@app.route("/videos")
def videos():
    return render_template("videos.html", posts=fetch_posts("video"))


@app.route("/author")
def author():
    return render_template("author.html", posts=fetch_posts())


@app.post("/login")
def login():
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()

    db = get_db()
    author_row = db.execute("SELECT email, password_hash FROM author WHERE id = 1").fetchone()

    if author_row and email == author_row["email"] and check_password_hash(author_row["password_hash"], password):
        session["author_logged_in"] = True
        flash("Вход выполнен. Панель автора открыта.", "success")
    else:
        flash("Неверная почта или пароль.", "error")
    return redirect(url_for("author"))


@app.post("/register-author")
def register_author():
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()
    confirm_password = request.form.get("confirm_password", "").strip()

    if not email or not password:
        flash("Заполни почту и пароль для автора.", "error")
        return redirect(url_for("author", mode="register"))

    if password != confirm_password:
        flash("Пароли не совпадают.", "error")
        return redirect(url_for("author", mode="register"))

    if len(password) < 6:
        flash("Пароль должен быть не короче 6 символов.", "error")
        return redirect(url_for("author", mode="register"))

    get_db().execute(
        "UPDATE author SET email = ?, password_hash = ? WHERE id = 1",
        (email, generate_password_hash(password)),
    )
    get_db().commit()
    session["author_logged_in"] = True
    flash("Автор зарегистрирован. Данные обновлены, панель открыта.", "success")
    return redirect(url_for("author"))


@app.post("/logout")
def logout():
    session.pop("author_logged_in", None)
    flash("Вы вышли из панели автора.", "info")
    return redirect(url_for("author"))


@app.post("/posts")
def create_post():
    if not is_author_logged_in():
        flash("Только автор может добавлять публикации.", "error")
        return redirect(url_for("author"))

    title = request.form.get("title", "").strip()
    category = request.form.get("category", "recipe").strip()
    description = request.form.get("description", "").strip()
    image_url = request.form.get("image_url", "").strip()
    video_url = request.form.get("video_url", "").strip()

    image_file = request.files.get("image_file")
    video_file = request.files.get("video_file")

    if not title or not description:
        flash("Заполни заголовок и описание публикации.", "error")
        return redirect(url_for("author"))

    if category not in {"recipe", "video"}:
        category = "recipe"

    try:
        if image_file and image_file.filename:
            image_url = save_uploaded_file(image_file, ALLOWED_IMAGE_EXTENSIONS)
        if video_file and video_file.filename:
            video_url = save_uploaded_file(video_file, ALLOWED_VIDEO_EXTENSIONS)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("author"))

    get_db().execute(
        """
        INSERT INTO posts (title, category, description, image_url, video_url)
        VALUES (?, ?, ?, ?, ?)
        """,
        (title, category, description, image_url, video_url),
    )
    get_db().commit()
    flash("Публикация добавлена.", "success")
    return redirect(url_for("author"))


@app.post("/comments")
def create_comment():
    post_id = request.form.get("post_id", "").strip()
    author_name = request.form.get("author_name", "").strip()
    body = request.form.get("body", "").strip()
    next_page = request.form.get("next_page", "home").strip()
    redirect_target = next_page if next_page in {"home", "recipes", "videos", "author"} else "home"

    if not post_id.isdigit():
        flash("Не удалось определить публикацию для комментария.", "error")
        return redirect(url_for(redirect_target))

    if not author_name or not body:
        flash("Чтобы оставить комментарий, укажи имя и текст.", "error")
        return redirect(url_for(redirect_target) + f"#post-{post_id}")

    if contains_bad_words(author_name) or contains_bad_words(body):
        flash("Комментарий содержит запрещенные слова и не был отправлен.", "error")
        return redirect(url_for(redirect_target) + f"#post-{post_id}")

    get_db().execute(
        """
        INSERT INTO comments (post_id, author_name, body)
        VALUES (?, ?, ?)
        """,
        (int(post_id), author_name, body),
    )
    get_db().commit()
    flash("Комментарий опубликован.", "success")
    return redirect(url_for(redirect_target) + f"#post-{post_id}")


@app.post("/posts/<int:post_id>/react/<reaction>")
def react(post_id: int, reaction: str):
    next_page = request.form.get("next_page", "home").strip()
    redirect_target = next_page if next_page in {"home", "recipes", "videos", "author"} else "home"

    if reaction not in REACTIONS:
        flash("Неизвестная реакция.", "error")
        return redirect(url_for(redirect_target) + f"#post-{post_id}")

    column = REACTIONS[reaction]["column"]
    get_db().execute(f"UPDATE posts SET {column} = {column} + 1 WHERE id = ?", (post_id,))
    get_db().commit()
    return redirect(url_for(redirect_target) + f"#post-{post_id}")


init_db()


if __name__ == "__main__":
    app.run(debug=True)
