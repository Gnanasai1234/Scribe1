# MySQL persistence

Scribe uses MySQL through SQLAlchemy. Set `DATABASE_URL` in the project root `.env`; the application does not fall back to another database.

```dotenv
DATABASE_URL=mysql+pymysql://root:YOUR_PASSWORD@localhost:3306/scribe?charset=utf8mb4
```

Create the database once, then apply the versioned schema:

```sql
CREATE DATABASE scribe CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

```powershell
cd backend
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Tables store documents, pages, chunks, summaries, summary claims, validation results, translations, audio metadata, and Q&A history. Extracted text, summary, claim, translation, and narration text use MySQL `LONGTEXT`; document text is stored in relational document rows and chunks in individual rows. Files remain in the configured local storage system.

The initial schema is `database/migrations/versions/0001_initial_mysql.py`. `alembic upgrade head` is the schema setup mechanism; application startup does not create tables.
