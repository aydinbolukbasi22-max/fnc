"""SQLAlchemy modelleri ve yardımcı fonksiyonlar."""
from datetime import date, datetime

from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


class User(db.Model):
    """Uygulamaya giriş yapabilecek temel kullanıcı modeli."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class Account(db.Model):
    """Gelir ve gider işlemlerinin bağlandığı finansal hesap modeli."""

    __tablename__ = "accounts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.String(255))
    currency = db.Column(db.String(3), nullable=False, default="TRY")

    transactions = db.relationship("Transaction", back_populates="account", cascade="all, delete-orphan")

    def balance(self) -> float:
        """Hesaba ait net bakiyeyi gelir - gider olarak hesaplar."""

        gelirler = sum(t.amount for t in self.transactions if t.type == "gelir")
        giderler = sum(t.amount for t in self.transactions if t.type == "gider")
        return gelirler - giderler


class Category(db.Model):
    """Gelir ve giderlerin gruplanacağı kategori modeli."""

    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    color = db.Column(db.String(30), nullable=False, default="primary")

    transactions = db.relationship("Transaction", back_populates="category", cascade="all, delete-orphan")


class Transaction(db.Model):
    """Gelir ve gider işlem tablosu."""

    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)
    description = db.Column(db.String(255))
    amount = db.Column(db.Float, nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    type = db.Column(db.String(10), nullable=False)  # gelir ya da gider
    emotion = db.Column(db.String(50))

    account = db.relationship("Account", back_populates="transactions")
    category = db.relationship("Category", back_populates="transactions")

    def signed_amount(self) -> float:
        """Gelir ve gideri toplu hesaplamalar için işaretli miktara çevirir."""

        return self.amount if self.type == "gelir" else -self.amount


class SavingsGoal(db.Model):
    """Tasarruf hedeflerini temsil eden model."""

    __tablename__ = "savings_goals"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    target_amount = db.Column(db.Float, nullable=False)
    start_date = db.Column(db.Date, nullable=False, default=date.today)
    target_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


__all__ = ["db", "User", "Account", "Category", "Transaction", "SavingsGoal"]
