"""SQLAlchemy modelleri ve yardımcı fonksiyonlar."""
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


class Account(db.Model):
    """Gelir ve gider işlemlerinin bağlandığı finansal hesap modeli."""

    __tablename__ = "accounts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.String(255))

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

    account = db.relationship("Account", back_populates="transactions")
    category = db.relationship("Category", back_populates="transactions")

    def signed_amount(self) -> float:
        """Gelir ve gideri toplu hesaplamalar için işaretli miktara çevirir."""

        return self.amount if self.type == "gelir" else -self.amount


__all__ = ["db", "Account", "Category", "Transaction"]
