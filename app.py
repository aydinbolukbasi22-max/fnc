"""Flask tabanlı Türkçe kişisel bütçe ve finans takip uygulaması."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from datetime import date, datetime, timedelta
from functools import wraps
from typing import Dict, List

from dateutil.relativedelta import relativedelta
from flask import (
    Flask,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from models import Account, Category, SavingsGoal, Transaction, User, db
from sqlalchemy import inspect, text
from werkzeug.security import check_password_hash, generate_password_hash


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

EMOTION_CHOICES = [
    ("mutluluk", "Mutluluk"),
    ("heyecan", "Heyecan"),
    ("rahatlama", "Rahatlama"),
    ("nötr", "Nötr"),
    ("pişmanlık", "Pişmanlık"),
    ("stres", "Stres"),
]

EMOTION_LABELS = dict(EMOTION_CHOICES)

GOAL_MILESTONES = [
    {
        "threshold": 25,
        "title": "İlk Çeyrek Tamam",
        "message": "Tasarruflarının ilk %25'ine ulaştın. Harcama günlüğünde küçük kaçamakları gözden geçir!",
        "variant": "info",
    },
    {
        "threshold": 50,
        "title": "Yarı Yolu Geçtin",
        "message": "%50 barajını aştın. Şimdi otomatik birikim talimatı oluşturarak ivmeni koruyabilirsin.",
        "variant": "success",
    },
    {
        "threshold": 75,
        "title": "Son Viraj",
        "message": "Tasarruf hedefinin %75'i tamam. Hedef tarihine kadar kalan küçük tutarları planlayarak temkinli ilerle.",
        "variant": "warning",
    },
    {
        "threshold": 100,
        "title": "Hedef Tamamlandı",
        "message": "Tebrikler! Hedefini gerçekleştirdin. Yeni bir hedef belirlemeyi ve başarılarını not etmeyi unutma.",
        "variant": "primary",
    },
]


def create_app() -> Flask:
    """Flask uygulamasını oluşturur ve yapılandırır."""

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "butce-uygulamasi"  # Demo amaçlı basit bir anahtar
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///butce.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    app.config.setdefault("_schema_initialized", False)

    def initialize_database(force: bool = False) -> None:
        """Uygulamanın ihtiyaç duyduğu tabloları ve kolonları güvenli şekilde oluşturur."""

        if app.config.get("_schema_initialized") and not force:
            return

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

        transaction_columns = {col["name"] for col in inspector.get_columns("transactions")}
        if "emotion" not in transaction_columns:
            db.session.execute(
                text("ALTER TABLE transactions ADD COLUMN emotion VARCHAR(50)")
            )
            db.session.commit()

        category_columns = {col["name"] for col in inspector.get_columns("categories")}
        if "monthly_limit" not in category_columns:
            db.session.execute(
                text("ALTER TABLE categories ADD COLUMN monthly_limit FLOAT")
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

        app.config["_schema_initialized"] = True

    with app.app_context():
        initialize_database()

    @app.template_filter("turkish_date")
    def turkish_date(value: date | datetime) -> str:
        """Tarihleri gg.aa.yyyy formatında gösteren şablon filtresi."""

        if value is None:
            return ""
        if isinstance(value, datetime):
            value = value.date()
        return value.strftime("%d.%m.%Y")

    @app.template_filter("format_amount")
    def format_amount(value: float | Decimal | None, decimal_places: int = 8) -> str:
        """Tutarları en fazla belirtilen basamakla formatlar."""

        if value is None:
            return "0"

        try:
            decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return str(value)

        quantize_exp = Decimal(f"1e-{decimal_places}")
        try:
            quantized = decimal_value.quantize(quantize_exp, rounding=ROUND_DOWN)
        except InvalidOperation:
            quantized = decimal_value

        formatted = format(quantized.normalize(), "f")
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
        return formatted or "0"

    @app.context_processor
    def inject_common_data() -> Dict[str, object]:
        """Şablonlarda sık kullanılan verileri otomatik olarak sağlar."""

        user = getattr(g, "user", None)
        accounts: List[Account] = []
        categories: List[Category] = []
        toplam_bakiye = 0.0
        toplam_gelir = 0.0
        toplam_gider = 0.0

        if user is not None:
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
            "emotion_choices": EMOTION_CHOICES,
            "emotion_labels": EMOTION_LABELS,
            "current_user": user,
        }

    @app.before_request
    def load_logged_in_user() -> None:
        """Oturum açmış kullanıcıyı global bağlama yükler."""

        user_id = session.get("user_id")
        g.user = db.session.get(User, user_id) if user_id else None

    def login_required(view):
        """Kullanıcı girişi gerektiren görünümler için dekoratör."""

        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if getattr(g, "user", None) is None:
                flash("Bu sayfaya erişmek için lütfen giriş yapın.", "warning")
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return wrapped_view

    @app.route("/register", methods=["GET", "POST"])
    def register():
        """Yeni kullanıcı kaydı oluşturur."""

        if getattr(g, "user", None):
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not email or not password:
                flash("E-posta ve şifre alanları zorunludur.", "danger")
            elif password != confirm_password:
                flash("Şifreler eşleşmiyor. Lütfen kontrol edin.", "danger")
            elif User.query.filter_by(email=email).first():
                flash("Bu e-posta adresiyle zaten bir hesap mevcut.", "warning")
            else:
                user = User(email=email, password_hash=generate_password_hash(password))
                db.session.add(user)
                db.session.commit()
                flash("Kayıt işlemi tamamlandı. Giriş yapabilirsiniz.", "success")
                return redirect(url_for("login"))

        return render_template("auth/register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        """Kullanıcı giriş işlemini gerçekleştirir."""

        if getattr(g, "user", None):
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")

            user = User.query.filter_by(email=email).first()
            if user and check_password_hash(user.password_hash, password):
                session.clear()
                session["user_id"] = user.id
                flash("Başarıyla giriş yaptınız.", "success")
                return redirect(url_for("dashboard"))

            flash("Geçersiz e-posta veya şifre.", "danger")

        return render_template("auth/login.html")

    @app.route("/logout")
    def logout():
        """Aktif kullanıcı oturumunu sonlandırır."""

        session.clear()
        flash("Oturumunuz sonlandırıldı.", "info")
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def dashboard():
        """Ana gösterge paneli."""

        bugun = date.today()
        ay_baslangic = bugun.replace(day=1)
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
            trend_verisi.append(
                {
                    "tarih": tarih.strftime("%d.%m.%Y"),
                    "tutar": gunluk_toplamlar.get(tarih, 0.0),
                }
            )
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

        duygu_ozeti_sorgu = (
            db.session.query(
                Transaction.emotion,
                db.func.count(Transaction.id).label("adet"),
                db.func.sum(Transaction.amount).label("toplam"),
            )
            .filter(Transaction.type == "gider")
            .filter(Transaction.date >= ay_baslangic)
            .filter(Transaction.emotion.isnot(None))
            .filter(Transaction.emotion != "")
            .group_by(Transaction.emotion)
            .order_by(db.desc("toplam"))
        )

        duygu_ozeti = [
            {
                "emotion": emotion,
                "label": EMOTION_LABELS.get(emotion, emotion.title()),
                "adet": adet,
                "toplam": toplam or 0,
                "ortalama": (toplam or 0) / adet if adet else 0,
            }
            for emotion, adet, toplam in duygu_ozeti_sorgu
        ]

        tasarruf_planlari, kilit_mesajlari = _tasarruf_planlarini_hazirla()

        kategori_limit_durumlari = _kategori_limit_durumlari()
        limitli_kategoriler = [
            durum for durum in kategori_limit_durumlari if durum["limit"] is not None
        ]
        limit_uyarilari = [durum for durum in limitli_kategoriler if durum["limit_asildi"]]

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
            duygu_ozeti=duygu_ozeti,
            tasarruf_planlari=tasarruf_planlari,
            kilit_mesajlari=kilit_mesajlari,
            kategori_limit_durumlari=kategori_limit_durumlari,
            limitli_kategoriler=limitli_kategoriler,
            limit_uyarilari=limit_uyarilari,
        )

    @app.route("/savings-goals", methods=["POST"])
    @login_required
    def create_savings_goal():
        """Yeni bir tasarruf hedefi oluşturur."""

        ad = request.form.get("name", "").strip()
        hedef_tutar = request.form.get("target_amount", type=float)
        baslangic_tarihi = _parse_date(request.form.get("start_date")) or date.today()
        hedef_tarihi = _parse_date(request.form.get("target_date"))

        if not ad or not hedef_tutar or hedef_tutar <= 0 or hedef_tarihi is None:
            flash("Hedef adı, tutarı ve hedef tarihi zorunludur.", "danger")
            return redirect(url_for("dashboard"))

        if hedef_tarihi < baslangic_tarihi:
            flash("Hedef tarihi başlangıç tarihinden önce olamaz.", "danger")
            return redirect(url_for("dashboard"))

        hedef = SavingsGoal(
            name=ad,
            target_amount=hedef_tutar,
            start_date=baslangic_tarihi,
            target_date=hedef_tarihi,
        )
        db.session.add(hedef)
        db.session.commit()
        flash("Tasarruf hedefi kaydedildi.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/savings-goals/<int:goal_id>/delete", methods=["POST"])
    @login_required
    def delete_savings_goal(goal_id: int):
        """Mevcut tasarruf hedefini siler."""

        hedef = SavingsGoal.query.get_or_404(goal_id)
        db.session.delete(hedef)
        db.session.commit()
        flash("Tasarruf hedefi silindi.", "info")
        return redirect(url_for("dashboard"))

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
    @login_required
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
    @login_required
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
    @login_required
    def delete_account(account_id: int):
        """Hesabı ve ilişkili işlemleri siler."""

        hesap = Account.query.get_or_404(account_id)
        db.session.delete(hesap)
        db.session.commit()
        flash("Hesap silindi.", "info")
        return redirect(url_for("accounts"))

    @app.route("/categories", methods=["GET", "POST"])
    @login_required
    def categories():
        """Kategori listesi ve ekleme işlemleri."""

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            color = request.form.get("color", "secondary").strip() or "secondary"
            limit = request.form.get("monthly_limit", type=float)
            if limit is not None and limit < 0:
                flash("Aylık limit negatif olamaz.", "danger")
            elif not name:
                flash("Kategori adı zorunludur.", "danger")
            else:
                kategori = Category(name=name, color=color, monthly_limit=limit)
                db.session.add(kategori)
                db.session.commit()
                flash("Kategori eklendi.", "success")
            return redirect(url_for("categories"))

        kategori_durumlari = _kategori_limit_durumlari()

        return render_template("categories.html", kategori_durumlari=kategori_durumlari)

    @app.route("/categories/<int:category_id>/update", methods=["POST"])
    @login_required
    def update_category(category_id: int):
        """Kategori bilgilerini günceller."""

        kategori = Category.query.get_or_404(category_id)
        kategori.name = request.form.get("name", kategori.name).strip()
        kategori.color = request.form.get("color", kategori.color).strip() or kategori.color
        limit = request.form.get("monthly_limit", type=float)
        if limit is not None and limit < 0:
            flash("Aylık limit negatif olamaz.", "danger")
            return redirect(url_for("categories"))
        kategori.monthly_limit = limit
        db.session.commit()
        flash("Kategori güncellendi.", "success")
        return redirect(url_for("categories"))

    @app.route("/categories/<int:category_id>/delete", methods=["POST"])
    @login_required
    def delete_category(category_id: int):
        """Kategoriyi siler."""

        kategori = Category.query.get_or_404(category_id)
        db.session.delete(kategori)
        db.session.commit()
        flash("Kategori silindi.", "info")
        return redirect(url_for("categories"))

    @app.route("/transactions", methods=["GET", "POST"])
    @login_required
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
            duygu = request.form.get("emotion", "").strip()

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
                    emotion=duygu or None,
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
    @login_required
    def update_transaction(transaction_id: int):
        """Var olan bir işlemi günceller."""

        islem = Transaction.query.get_or_404(transaction_id)
        tarih = _parse_date(request.form.get("date")) or islem.date
        islem.date = tarih
        islem.category_id = request.form.get("category_id", type=int) or islem.category_id
        islem.account_id = request.form.get("account_id", type=int) or islem.account_id
        islem.type = request.form.get("type", islem.type)
        islem.description = request.form.get("description", islem.description)
        duygu = request.form.get("emotion", "").strip()
        islem.emotion = duygu or None
        amount = request.form.get("amount", type=float)
        if amount:
            islem.amount = amount
        db.session.commit()
        flash("İşlem güncellendi.", "success")
        return redirect(url_for("transactions"))

    @app.route("/transactions/<int:transaction_id>/delete", methods=["POST"])
    @login_required
    def delete_transaction(transaction_id: int):
        """İşlemi siler."""

        islem = Transaction.query.get_or_404(transaction_id)
        db.session.delete(islem)
        db.session.commit()
        flash("İşlem silindi.", "info")
        return redirect(url_for("transactions"))

    @app.route("/reports")
    @login_required
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

    def _kategori_limit_durumlari() -> List[Dict[str, object]]:
        """Kategorilerin aylık limitlerine göre durum özetini hazırlar."""

        ay_baslangic = date.today().replace(day=1)
        sonraki_ay = ay_baslangic + relativedelta(months=1)

        aylik_harcamalar = {
            kategori_id: toplam or 0.0
            for kategori_id, toplam in (
                db.session.query(
                    Transaction.category_id,
                    db.func.sum(Transaction.amount).label("toplam"),
                )
                .filter(Transaction.type == "gider")
                .filter(Transaction.date >= ay_baslangic)
                .filter(Transaction.date < sonraki_ay)
                .group_by(Transaction.category_id)
                .all()
            )
        }

        durumlar: List[Dict[str, object]] = []
        for kategori in Category.query.order_by(Category.name.asc()).all():
            limit = kategori.monthly_limit
            aylik_harcama = float(aylik_harcamalar.get(kategori.id, 0.0))

            limit_asildi = False
            kalan_limit = None
            kullanilan_oran = None

            if limit is not None:
                limit_asildi = aylik_harcama > limit if limit > 0 else aylik_harcama > 0
                kalan_limit = limit - aylik_harcama
                if limit > 0:
                    kullanilan_oran = (aylik_harcama / limit) * 100
                else:
                    kullanilan_oran = 100.0 if limit_asildi else 0.0

            durumlar.append(
                {
                    "kategori": kategori,
                    "limit": limit,
                    "aylik_harcama": aylik_harcama,
                    "kalan_limit": kalan_limit,
                    "limit_asildi": limit_asildi,
                    "kullanilan_oran": kullanilan_oran,
                }
            )

        return durumlar

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

    def _ay_sayisi(baslangic: date, bitis: date) -> int:
        """İki tarih arasındaki ay sayısını (en az 1) hesaplar."""

        if bitis < baslangic:
            return 0
        delta = relativedelta(bitis, baslangic)
        ay_farki = delta.years * 12 + delta.months
        if delta.days >= 0:
            ay_farki += 1
        return max(ay_farki, 1)

    def _tasarruf_planlarini_hazirla() -> tuple[List[Dict[str, object]], List[Dict[str, object]]]:
        """Tasarruf hedeflerini detaylandırır ve kilit mesajlarını döndürür."""

        bugun = date.today()
        planlar: List[Dict[str, object]] = []
        kilit_mesajlari: List[Dict[str, object]] = []

        tum_hedefler = SavingsGoal.query.order_by(SavingsGoal.target_date.asc()).all()
        for hedef in tum_hedefler:
            baslangic = hedef.start_date
            hedef_tarihi = hedef.target_date
            if isinstance(baslangic, datetime):
                baslangic = baslangic.date()
            if isinstance(hedef_tarihi, datetime):
                hedef_tarihi = hedef_tarihi.date()

            plan_suresi = max(_ay_sayisi(baslangic, hedef_tarihi), 1)
            takip_bitis = min(hedef_tarihi, bugun)
            gelir_toplam = (
                db.session.query(db.func.sum(Transaction.amount))
                .filter(Transaction.type == "gelir")
                .filter(Transaction.date >= baslangic)
                .filter(Transaction.date <= takip_bitis)
                .scalar()
                or 0
            )
            gider_toplam = (
                db.session.query(db.func.sum(Transaction.amount))
                .filter(Transaction.type == "gider")
                .filter(Transaction.date >= baslangic)
                .filter(Transaction.date <= takip_bitis)
                .scalar()
                or 0
            )
            net_birikim = gelir_toplam - gider_toplam
            kalan_tutar = max(0.0, hedef.target_amount - net_birikim)

            hedefe_kalan_gun = (hedef_tarihi - bugun).days
            hedefe_kalan_gun = hedefe_kalan_gun if hedefe_kalan_gun >= 0 else 0

            ilerleme = 0.0
            if hedef.target_amount > 0:
                ilerleme = max(0.0, min(100.0, (net_birikim / hedef.target_amount) * 100))

            gecen_ay_sayisi = 0
            if bugun >= baslangic:
                gecen_ay_sayisi = _ay_sayisi(baslangic, takip_bitis)

            ortalama_aylik = net_birikim / gecen_ay_sayisi if gecen_ay_sayisi else 0.0
            onerilen_aylik = hedef.target_amount / plan_suresi if plan_suresi else hedef.target_amount

            kilitler: List[Dict[str, object]] = []
            for milestone in GOAL_MILESTONES:
                if ilerleme >= milestone["threshold"]:
                    kilit = {
                        **milestone,
                        "goal_name": hedef.name,
                    }
                    kilitler.append(kilit)
                    kilit_mesajlari.append(kilit)

            planlar.append(
                {
                    "id": hedef.id,
                    "name": hedef.name,
                    "target_amount": hedef.target_amount,
                    "start_date": baslangic,
                    "target_date": hedef_tarihi,
                    "net_savings": net_birikim,
                    "remaining_amount": kalan_tutar,
                    "remaining_days": hedefe_kalan_gun,
                    "progress": ilerleme,
                    "recommended_monthly": onerilen_aylik,
                    "actual_monthly": ortalama_aylik,
                    "is_completed": ilerleme >= 100,
                    "unlocked_milestones": kilitler,
                    "total_months": plan_suresi,
                    "elapsed_months": gecen_ay_sayisi,
                }
            )

        kilit_mesajlari.sort(key=lambda item: (item["threshold"], item["goal_name"]))
        return planlar, kilit_mesajlari

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
