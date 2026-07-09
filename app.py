import os
import uuid
import io
from flask import Flask, render_template, redirect, url_for, request, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import qrcode
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-123')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///bus_system.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    bookings = db.relationship('Booking', backref='passenger', lazy=True)

class Bus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bus_number = db.Column(db.String(50), unique=True, nullable=False)
    total_seats = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    route_id = db.Column(db.Integer, db.ForeignKey('route.id'), nullable=False)
    bookings = db.relationship('Booking', backref='bus', lazy=True)

class Route(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    origin = db.Column(db.String(100), nullable=False)
    destination = db.Column(db.String(100), nullable=False)
    departure_time = db.Column(db.String(50), nullable=False)
    buses = db.relationship('Bus', backref='route', lazy=True)

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_uuid = db.Column(db.String(100), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    bus_id = db.Column(db.Integer, db.ForeignKey('bus.id'), nullable=False)
    seat_number = db.Column(db.Integer, nullable=False)
    travel_date = db.Column(db.String(50), nullable=False)
    booking_date = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('bus_id', 'seat_number', 'travel_date', name='_bus_seat_date_uc'),)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- ROUTES ---
@app.route('/')
def index():
    routes = Route.query.all()
    search_results = None
    origin = request.args.get('origin')
    destination = request.args.get('destination')
    if origin and destination:
        search_results = Route.query.filter(Route.origin.like(f"%{origin}%"), Route.destination.like(f"%{destination}%")).all()
    return render_template('index.html', routes=routes, search_results=search_results)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        if User.query.filter_by(email=email).first():
            flash('Email already exists.', 'danger')
            return redirect(url_for('register'))
        hashed_pw = generate_password_hash(password, method='scrypt')
        new_user = User(username=username, email=email, password=hashed_pw)
        if User.query.count() == 0:
            new_user.is_admin = True
        db.session.add(new_user)
        db.session.commit()
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
        flash('Invalid credentials.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/book/<int:bus_id>', methods=['POST'])
@login_required
def book_ticket(bus_id):
    seat = request.form.get('seat_number')
    date = request.form.get('travel_date')
    if not seat or not date:
        flash('Please select a seat and date.', 'warning')
        return redirect(url_for('index'))
    exists = Booking.query.filter_by(bus_id=bus_id, seat_number=seat, travel_date=date).first()
    if exists:
        flash('This seat is already booked for this date!', 'danger')
        return redirect(url_for('index'))
    ticket_id = str(uuid.uuid4())[:8].upper()
    new_booking = Booking(ticket_uuid=ticket_id, user_id=current_user.id, bus_id=bus_id, seat_number=seat, travel_date=date)
    db.session.add(new_booking)
    db.session.commit()
    flash(f'Ticket Booked Successfully! ID: {ticket_id}', 'success')
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
@login_required
def dashboard():
    bookings = Booking.query.filter_by(user_id=current_user.id).all()
    return render_template('dashboard.html', bookings=bookings)

@app.route('/cancel/<int:booking_id>', methods=['POST'])
@login_required
def cancel_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id == current_user.id or current_user.is_admin:
        db.session.delete(booking)
        db.session.commit()
        flash('Booking cancelled successfully.', 'info')
    return redirect(url_for('dashboard'))

@app.route('/download-ticket/<int:booking_id>')
@login_required
def download_ticket(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id and not current_user.is_admin:
        return "Unauthorized", 403
    qr_data = f"Ticket: {booking.ticket_uuid}\nPassenger: {booking.passenger.username}\nBus: {booking.bus.bus_number}"
    qr = qrcode.make(qr_data)
    qr_img_io = io.BytesIO()
    qr.save(qr_img_io, 'PNG')
    qr_img_io.seek(0)
    pdf_io = io.BytesIO()
    p = canvas.Canvas(pdf_io)
    p.drawString(100, 750, "=== DIGITAL BUS PASS ===")
    p.drawString(100, 720, f"Ticket ID: {booking.ticket_uuid}")
    p.drawString(100, 700, f"Passenger: {booking.passenger.username}")
    p.drawString(100, 680, f"Bus Number: {booking.bus.bus_number}")
    p.drawString(100, 660, f"Route: {booking.bus.route.origin} -> {booking.bus.route.destination}")
    p.drawString(100, 640, f"Seat: {booking.seat_number} | Date: {booking.travel_date}")
    p.drawString(100, 620, f"Price Paid: ${booking.bus.price}")
    qr_reader = ImageReader(qr_img_io)
    p.drawImage(qr_reader, 100, 450, width=120, height=120)
    p.showPage()
    p.save()
    pdf_io.seek(0)
    return send_file(pdf_io, mimetype='application/pdf', as_attachment=True, download_name=f'Ticket_{booking.ticket_uuid}.pdf')

@app.route('/admin')
@login_required
def admin_panel():
    if not current_user.is_admin:
        flash('Access Denied.', 'danger')
        return redirect(url_for('index'))
    buses = Bus.query.all()
    routes = Route.query.all()
    users = User.query.all()
    bookings = Booking.query.all()
    stats = {'total_users': len(users), 'total_bookings': len(bookings), 'total_revenue': sum(b.bus.price for b in bookings)}
    return render_template('admin.html', buses=buses, routes=routes, users=users, bookings=bookings, stats=stats)

@app.route('/admin/add-route', methods=['POST'])
@login_required
def add_route():
    if current_user.is_admin:
        new_route = Route(origin=request.form.get('origin'), destination=request.form.get('destination'), departure_time=request.form.get('departure_time'))
        db.session.add(new_route)
        db.session.commit()
    return redirect(url_for('admin_panel'))

@app.route('/admin/add-bus', methods=['POST'])
@login_required
def add_bus():
    if current_user.is_admin:
        new_bus = Bus(bus_number=request.form.get('bus_number'), total_seats=int(request.form.get('total_seats')), price=float(request.form.get('price')), route_id=int(request.form.get('route_id')))
        db.session.add(new_bus)
        db.session.commit()
    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
