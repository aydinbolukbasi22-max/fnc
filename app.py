"""Flask tabanlı Türkçe kişisel bütçe ve finans takip uygulaması."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List

from dateutil.relativedelta import relativedelta
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from models import Account, Category, Transaction, db
from sqlalchemy import inspect, text


DEFAULT_CURRENCY = "TRY"
CURRENCY_SYMBOLS = {
    "TRY": "₺",
    "USD": "$",
    "EUR": "€",
}
CURRENCY_CHOICES = [
    ("TRY", "Türk Lirası (₺)"),
    ("USD", "ABD Doları ($)"),
    ("EUR", "Euro (€)"),
]


def create_app() -> Flask:
    """Flask uygulamasını oluşturur ve yapılandırır."""

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "butce-uygulamasi"  # Demo amaçlı basit bir anahtar
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///butce.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    with app.app_context():
        db.create_all()

        inspector = inspect(db.engine)
        account_columns = {col["name"] for col in inspector.get_columns("accounts")}
        if "currency" not in account_columns:
            db.session.execute(
                text(
                    "ALTER TABLE accounts ADD COLUMN currency VARCHAR(3) NOT NULL DEFAULT 'TRY'"
                )
            )
            db.session.commit()

        # Uygulama ilk kez açıldığında varsayılan kayıtlar oluşturalım.
        if not Account.query.first():
            db.session.add_all(
                [
                    Account(name="Nakit", description="Cüzdandaki para", currency=DEFAULT_CURRENCY),
                    Account(name="Banka", description="Vadesiz hesap", currency=DEFAULT_CURRENCY),
                    Account(
                        name="Kredi Kartı",
                        description="Kart harcamaları",
                        currency=DEFAULT_CURRENCY,
                    ),
                ]
            )
            db.session.commit()
        if not Category.query.first():
            db.session.add_all(
                [
                    Category(name="Maaş", color="success"),
                    Category(name="Market", color="warning"),
                    Category(name="Faturalar", color="danger"),
                    Category(name="Diğer", color="secondary"),
                ]
            )
            db.session.commit()

    @app.template_filter("turkish_date")
    def turkish_date(value: date | datetime) -> str:
        """Tarihleri gg.aa.yyyy formatında gösteren şablon filtresi."""

        if value is None:
            return ""
        if isinstance(value, datetime):
            value = value.date()
        return value.strftime("%d.%m.%Y")

    @app.context_processor
    def inject_common_data() -> Dict[str, object]:
        """Şablonlarda sık kullanılan verileri otomatik olarak sağlar."""

        accounts = Account.query.all()
        categories = Category.query.all()
        toplam_bakiye = sum(a.balance() for a in accounts)
        toplam_gelir = sum(
            t.amount for t in Transaction.query.filter_by(type="gelir").all()
        )
        toplam_gider = sum(
            t.amount for t in Transaction.query.filter_by(type="gider").all()
        )
        return {
            "tum_hesaplar": accounts,
            "tum_kategoriler": categories,
            "toplam_bakiye": toplam_bakiye,
            "toplam_gelir": toplam_gelir,
            "toplam_gider": toplam_gider,
            "now": datetime.now,
            "currency_symbols": CURRENCY_SYMBOLS,
            "default_currency": DEFAULT_CURRENCY,
            "default_currency_symbol": CURRENCY_SYMBOLS[DEFAULT_CURRENCY],
        }

    @app.route("/")
    def dashboard():
        """Ana gösterge paneli."""

        son_otuz_gun = date.today() - timedelta(days=29)
        son_otuz_gun_islemleri = (
            Transaction.query.filter(Transaction.date >= son_otuz_gun)
            .order_by(Transaction.date.asc())
            .all()
        )

        gunluk_toplamlar: Dict[date, float] = defaultdict(float)
        for t in son_otuz_gun_islemleri:
            gunluk_toplamlar[t.date] += t.signed_amount()
        trend_verisi = []
        tarih = son_otuz_gun
        while tarih <= date.today():
            trend_verisi.append({
                "tarih": tarih.strftime("%d.%m.%Y"),
                "tutar": gunluk_toplamlar.get(tarih, 0.0),
            })
            tarih += timedelta(days=1)

        gelirler = (
            db.session.query(db.func.sum(Transaction.amount))
            .filter(Transaction.type == "gelir")
            .scalar()
            or 0
        )
        giderler = (
            db.session.query(db.func.sum(Transaction.amount))
            .filter(Transaction.type == "gider")
            .scalar()
            or 0
        )
        net_bakiye = gelirler - giderler

        kategori_toplamlari = (
            db.session.query(
                Category.name,
                Category.color,
                db.func.sum(Transaction.amount).label("toplam"),
            )
            .join(Transaction, Transaction.category_id == Category.id)
            .filter(Transaction.type == "gider")
            .group_by(Category.id)
            .order_by(db.desc("toplam"))
            .all()
        )
        en_cok_harcanan = kategori_toplamlari[0] if kategori_toplamlari else None

        hesap_bakiyeleri = [
            {"hesap": hesap.name, "bakiye": hesap.balance()} for hesap in Account.query.all()
        ]

        aylik_veriler = _aylik_gelir_gider_dagilimi()

        return render_template(
            "dashboard.html",
            gelirler=gelirler,
            giderler=giderler,
            net_bakiye=net_bakiye,
            kategori_toplamlari=kategori_toplamlari,
            en_cok_harcanan=en_cok_harcanan,
            trend_verisi=trend_verisi,
            hesap_bakiyeleri=hesap_bakiyeleri,
            aylik_veriler=aylik_veriler,
        )

    def _parse_date(value: str | None) -> date | None:
        """Formlardan gelen tarih değerlerini Türkçe format desteğiyle çözümler."""

        if not value:
            return None
        for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        return None

    @app.route("/accounts", methods=["GET", "POST"])
    def accounts():
        """Hesap listesi ve ekleme işlemleri."""

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            description = request.form.get("description", "").strip()
            currency = request.form.get("currency", DEFAULT_CURRENCY).upper()
            if currency not in CURRENCY_SYMBOLS:
                currency = DEFAULT_CURRENCY
            if not name:
                flash("Hesap adı zorunludur.", "danger")
            else:
                hesap = Account(name=name, description=description, currency=currency)
                db.session.add(hesap)
                db.session.commit()
                flash("Hesap başarıyla eklendi.", "success")
            return redirect(url_for("accounts"))

        hesaplar = Account.query.order_by(Account.name.asc()).all()
        return render_template(
            "accounts.html",
            hesaplar=hesaplar,
            para_birimleri=CURRENCY_CHOICES,
        )

    @app.route("/accounts/<int:account_id>/update", methods=["POST"])
    def update_account(account_id: int):
        """Hesap bilgilerini günceller."""

        hesap = Account.query.get_or_404(account_id)
        hesap.name = request.form.get("name", hesap.name).strip()
        hesap.description = request.form.get("description", hesap.description).strip()
        currency = request.form.get("currency", hesap.currency or DEFAULT_CURRENCY).upper()
        if currency not in CURRENCY_SYMBOLS:
            currency = DEFAULT_CURRENCY
        hesap.currency = currency
        db.session.commit()
        flash("Hesap güncellendi.", "success")
        return redirect(url_for("accounts"))

    @app.route("/accounts/<int:account_id>/delete", methods=["POST"])
    def delete_account(account_id: int):
        """Hesabı ve ilişkili işlemleri siler."""

        hesap = Account.query.get_or_404(account_id)
        db.session.delete(hesap)
        db.session.commit()
        flash("Hesap silindi.", "info")
        return redirect(url_for("accounts"))

    @app.route("/categories", methods=["GET", "POST"])
    def categories():
        """Kategori listesi ve ekleme işlemleri."""

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            color = request.form.get("color", "secondary").strip() or "secondary"
            if not name:
                flash("Kategori adı zorunludur.", "danger")
            else:
                kategori = Category(name=name, color=color)
                db.session.add(kategori)
                db.session.commit()
                flash("Kategori eklendi.", "success")
            return redirect(url_for("categories"))

        kategoriler = Category.query.order_by(Category.name.asc()).all()
        return render_template("categories.html", kategoriler=kategoriler)

    @app.route("/categories/<int:category_id>/update", methods=["POST"])
    def update_category(category_id: int):
        """Kategori bilgilerini günceller."""

        kategori = Category.query.get_or_404(category_id)
        kategori.name = request.form.get("name", kategori.name).strip()
        kategori.color = request.form.get("color", kategori.color).strip() or kategori.color
        db.session.commit()
        flash("Kategori güncellendi.", "success")
        return redirect(url_for("categories"))

    @app.route("/categories/<int:category_id>/delete", methods=["POST"])
    def delete_category(category_id: int):
        """Kategoriyi siler."""

        kategori = Category.query.get_or_404(category_id)
        db.session.delete(kategori)
        db.session.commit()
        flash("Kategori silindi.", "info")
        return redirect(url_for("categories"))

    @app.route("/transactions", methods=["GET", "POST"])
    def transactions():
        """Gelir ve gider işlemlerini listeler ve yeni kayıt ekler."""

        if request.method == "POST":
            tarih_str = request.form.get("date")
            tarih = _parse_date(tarih_str) or date.today()
            kategori_id = request.form.get("category_id", type=int)
            hesap_id = request.form.get("account_id", type=int)
            tur = request.form.get("type", "gider")
            aciklama = request.form.get("description", "").strip()
            miktar = request.form.get("amount", type=float)

            if not (kategori_id and hesap_id and miktar):
                flash("Kategori, hesap ve tutar alanları zorunludur.", "danger")
            else:
                islem = Transaction(
                    date=tarih,
                    category_id=kategori_id,
                    account_id=hesap_id,
                    type=tur,
                    description=aciklama,
                    amount=miktar,
                )
                db.session.add(islem)
                db.session.commit()
                flash("İşlem eklendi.", "success")
            return redirect(url_for("transactions"))

        baslangic = _parse_date(request.args.get("start_date"))
        bitis = _parse_date(request.args.get("end_date"))
        kategori_id = request.args.get("category_id", type=int)
        tur = request.args.get("type", default="tumu")

        sorgu = Transaction.query.order_by(Transaction.date.desc(), Transaction.id.desc())
        if baslangic:
            sorgu = sorgu.filter(Transaction.date >= baslangic)
        if bitis:
            sorgu = sorgu.filter(Transaction.date <= bitis)
        if kategori_id:
            sorgu = sorgu.filter(Transaction.category_id == kategori_id)
        if tur in {"gelir", "gider"}:
            sorgu = sorgu.filter(Transaction.type == tur)

        islemler = sorgu.all()

        toplam = sum(t.signed_amount() for t in islemler)

        return render_template(
            "transactions.html",
            islemler=islemler,
            toplam=toplam,
            filtreler={
                "start_date": baslangic.strftime("%Y-%m-%d") if baslangic else "",
                "end_date": bitis.strftime("%Y-%m-%d") if bitis else "",
                "category_id": kategori_id or "",
                "type": tur,
            },
        )

    @app.route("/transactions/<int:transaction_id>/update", methods=["POST"])
    def update_transaction(transaction_id: int):
        """Var olan bir işlemi günceller."""

        islem = Transaction.query.get_or_404(transaction_id)
        tarih = _parse_date(request.form.get("date")) or islem.date
        islem.date = tarih
        islem.category_id = request.form.get("category_id", type=int) or islem.category_id
        islem.account_id = request.form.get("account_id", type=int) or islem.account_id
        islem.type = request.form.get("type", islem.type)
        islem.description = request.form.get("description", islem.description)
        amount = request.form.get("amount", type=float)
        if amount:
            islem.amount = amount
        db.session.commit()
        flash("İşlem güncellendi.", "success")
        return redirect(url_for("transactions"))

    @app.route("/transactions/<int:transaction_id>/delete", methods=["POST"])
    def delete_transaction(transaction_id: int):
        """İşlemi siler."""

        islem = Transaction.query.get_or_404(transaction_id)
        db.session.delete(islem)
        db.session.commit()
        flash("İşlem silindi.", "info")
        return redirect(url_for("transactions"))

    @app.route("/reports")
    def reports():
        """Grafik raporlarını gösterir."""

        aylik_veriler = _aylik_gelir_gider_dagilimi()
        kategori_dagilimi = _kategori_dagilimi()
        hesap_dagilimi = [
            {"label": hesap.name, "value": round(hesap.balance(), 2)}
            for hesap in Account.query.order_by(Account.name)
        ]

        return render_template(
            "reports.html",
            aylik_veriler=aylik_veriler,
            kategori_dagilimi=kategori_dagilimi,
            hesap_dagilimi=hesap_dagilimi,
        )

    def _aylik_gelir_gider_dagilimi() -> Dict[str, List]:
        """Son 6 ayın gelir/gider dağılımını çıkarır."""

        bugun = date.today().replace(day=1)
        aylar: List[str] = []
        gelir_listesi: List[float] = []
        gider_listesi: List[float] = []

        for i in range(5, -1, -1):
            ay_baslangic = bugun - relativedelta(months=i)
            sonraki_ay = ay_baslangic + relativedelta(months=1)
            ay_etiketi = ay_baslangic.strftime("%m.%Y")
            aylar.append(ay_etiketi)

            ay_gelir = (
                db.session.query(db.func.sum(Transaction.amount))
                .filter(Transaction.type == "gelir")
                .filter(Transaction.date >= ay_baslangic)
                .filter(Transaction.date < sonraki_ay)
                .scalar()
                or 0
            )
            ay_gider = (
                db.session.query(db.func.sum(Transaction.amount))
                .filter(Transaction.type == "gider")
                .filter(Transaction.date >= ay_baslangic)
                .filter(Transaction.date < sonraki_ay)
                .scalar()
                or 0
            )
            gelir_listesi.append(round(ay_gelir, 2))
            gider_listesi.append(round(ay_gider, 2))

        return {"labels": aylar, "gelir": gelir_listesi, "gider": gider_listesi}

    def _kategori_dagilimi() -> List[Dict[str, object]]:
        """Gelir ve giderlerin kategori bazında dağılımını hesaplar."""

        sorgu = (
            db.session.query(
                Category.name,
                Category.color,
                db.func.sum(Transaction.amount).label("toplam"),
                Transaction.type,
            )
            .join(Transaction, Transaction.category_id == Category.id)
            .group_by(Category.id, Transaction.type)
            .order_by(Category.name)
        )

        dagilim: Dict[str, Dict[str, object]] = {}
        for isim, renk, toplam, tur in sorgu:
            if isim not in dagilim:
                dagilim[isim] = {
                    "name": isim,
                    "color": renk,
                    "gelir": 0.0,
                    "gider": 0.0,
                }
            dagilim[isim][tur] = round(toplam or 0, 2)
        return list(dagilim.values())

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(debug=True)
