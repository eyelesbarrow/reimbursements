

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
import os
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file


class DatabaseConnection():
    def __init__(self, db_path: str):
        # Use environment variable or default to SQLite for testing

        os.makedirs(os.path.dirname(db_path) if db_path else 'data', exist_ok=True)

        self.db_path = db_path or 'data/database.db'
        self.engine = create_engine(f'sqlite:///{self.db_path}', echo=False)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def get_db_session(self) -> Session:
        return self.SessionLocal()

    def _test_db_connection(self):
        try:
            with self.SessionLocal() as session:
                session.execute(text('SELECT 1'))
            print("Database connection successful.")
        except Exception as e:
            print(f"Database connection failed: {e}")
            raise

    def list_tables(self):
        inspector = inspect(self.engine)
        tables = inspector.get_table_names()
        for table in tables:
            with self.get_db_session() as session:
                result = session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.scalar()
            print(f"Table: {table}, Row Count: {count}")

    def get_table_schema(self, table_name: str):
        inspector = inspect(self.engine)
        columns = inspector.get_columns(table_name)
        print(f"Schema for table '{table_name}':")
        for column in columns:
            print(f" - {column['name']} ({column['type']})")

if __name__ == "__main__":
    db_path = os.getenv('db_path', 'data/database.db')
    print(f"Using database path: {db_path}")
    db_connection = DatabaseConnection(db_path)
    db_connection._test_db_connection()
    tables = db_connection.list_tables()
    print("Tables in the database:", tables)


