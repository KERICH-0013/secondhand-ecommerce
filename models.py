from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    fullname = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(15))
    role = db.Column(db.Enum('buyer', 'seller', 'admin'), default='buyer')
    is_verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Numeric(10,2), nullable=False)
    category = db.Column(db.String(50))
    condition = db.Column(db.Enum('new', 'like-new', 'good', 'fair', 'poor'))
    image_url = db.Column(db.String(255))
    status = db.Column(db.Enum('available', 'sold', 'pending'), default='available')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship to User (seller)
    seller = db.relationship('User', foreign_keys=[seller_id], backref='products')

class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'))
    quantity = db.Column(db.Integer, default=1)
    total_price = db.Column(db.Numeric(10,2))
    order_status = db.Column(db.Enum('pending', 'confirmed', 'shipped', 'delivered', 'cancelled'), default='pending')
    order_date = db.Column(db.DateTime, default=datetime.utcnow)

    # M-Pesa integration fields
    checkout_request_id = db.Column(db.String(100), nullable=True)
    payment_status = db.Column(db.Enum('pending', 'paid', 'failed'), default='pending')
    mpesa_receipt_number = db.Column(db.String(50), nullable=True)

    product = db.relationship("Product", backref="orders")

class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    receiver_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'))
    message = db.Column(db.Text)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)


# ========== WISHLIST MODEL ==========
class Wishlist(db.Model):
    __tablename__ = 'wishlist'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='wishlist_items')
    product = db.relationship('Product', backref='wished_by')