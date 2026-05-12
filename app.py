from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
import time
from datetime import datetime, timedelta
from config import Config
from models import db, User, Product, Order, Wishlist, Message
from sqlalchemy import func
from dotenv import load_dotenv
import requests
import base64
import json

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config.from_object(Config)

# Initialize extensions
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ---------- M-PESA DIRECT API CONFIGURATION ----------
consumer_key = os.getenv('CONSUMER_KEY')
consumer_secret = os.getenv('CONSUMER_SECRET')
passkey = os.getenv('PASSKEY')
shortcode = os.getenv('SHORTCODE')
callback_url = os.getenv('CALLBACK_URL')
environment = os.getenv('ENVIRONMENT', 'sandbox')

def get_mpesa_token():
    """Generate OAuth token for M-Pesa API."""
    api_url = 'https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials'
    if environment == 'production':
        api_url = 'https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials'

    response = requests.get(api_url, auth=(consumer_key, consumer_secret))
    print(f"Token response status: {response.status_code}")
    print(f"Token response body: {response.text[:200]}")

    if response.status_code == 200:
        try:
            return response.json().get('access_token')
        except:
            return None
    else:
        return None

def initiate_mpesa_payment(phone_number, amount, account_reference, transaction_desc):
    """Send STK push using direct API calls."""
    phone_number = ''.join(filter(str.isdigit, phone_number))
    if phone_number.startswith('0'):
        phone_number = '254' + phone_number[1:]
    elif phone_number.startswith('254'):
        pass
    elif phone_number.startswith('+254'):
        phone_number = phone_number[1:]
    else:
        phone_number = '254' + phone_number
    if len(phone_number) != 12:
        return {'ResponseCode': '1', 'errorMessage': f'Phone number must be 12 digits (254...). Got: {phone_number}'}

    access_token = get_mpesa_token()
    if not access_token:
        return {'ResponseCode': '1', 'errorMessage': 'Failed to get M-Pesa token. Check credentials.'}

    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    password_str = f"{shortcode}{passkey}{timestamp}"
    password = base64.b64encode(password_str.encode()).decode()

    api_url = 'https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest'
    if environment == 'production':
        api_url = 'https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest'

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    payload = {
        'BusinessShortCode': shortcode,
        'Password': password,
        'Timestamp': timestamp,
        'TransactionType': 'CustomerPayBillOnline',
        'Amount': int(amount),
        'PartyA': phone_number,
        'PartyB': shortcode,
        'PhoneNumber': phone_number,
        'CallBackURL': callback_url,
        'AccountReference': account_reference,
        'TransactionDesc': transaction_desc
    }

    response = requests.post(api_url, json=payload, headers=headers)
    print(f"STK push response status: {response.status_code}")
    print(f"STK push response body: {response.text[:500]}")

    try:
        return response.json()
    except:
        return {'ResponseCode': '1', 'errorMessage': f'API returned non-JSON: {response.status_code} - {response.text[:200]}'}

# Create tables (run once)
with app.app_context():
    db.create_all()

# ---------- ROUTES ----------
@app.route('/')
def index():
    latest_products = Product.query.filter_by(status='available').order_by(Product.created_at.desc()).limit(6).all()
    return render_template('index.html', products=latest_products)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        fullname = request.form.get('fullname')
        email = request.form.get('email')
        phone = request.form.get('phone')
        password = request.form.get('password')
        role = request.form.get('role')
        if not fullname or not email or not password:
            flash('All fields are required.', 'danger')
            return redirect(url_for('register'))
        existing = User.query.filter_by(email=email).first()
        if existing:
            flash('Email already registered. Please log in.', 'danger')
            return redirect(url_for('login'))
        hashed_pw = generate_password_hash(password)
        new_user = User(
            fullname=fullname,
            email=email,
            phone=phone,
            password=hashed_pw,
            role=role,
            is_verified=False
        )
        db.session.add(new_user)
        db.session.commit()
        flash('Registration successful! You can now log in.', 'success')
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
            flash(f'Welcome back, {user.fullname}!', 'success')
            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

# ========== DASHBOARD (SELLER + BUYER) ==========
@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'seller':
        my_products = Product.query.filter_by(seller_id=current_user.id).all()
        total_products = len(my_products)
        orders = db.session.query(Order).join(Product).filter(Product.seller_id == current_user.id).all()
        total_sold = sum(o.quantity for o in orders if o.order_status == 'delivered')
        total_revenue = sum(o.total_price for o in orders if o.order_status == 'delivered')
        pending_orders = sum(1 for o in orders if o.order_status == 'pending')
        recent = db.session.query(
            Order.id, Order.quantity, Order.total_price, Order.order_status, Order.order_date,
            Product.title.label('product_title'), User.fullname.label('buyer_name')
        ).join(Product, Order.product_id == Product.id
        ).join(User, Order.buyer_id == User.id
        ).filter(Product.seller_id == current_user.id
        ).order_by(Order.order_date.desc()).limit(10).all()
        today = datetime.utcnow().date()
        week_labels, week_values = [], []
        for i in range(6, -1, -1):
            day = today - timedelta(days=i)
            week_labels.append(day.strftime('%a, %b %d'))
            day_total = db.session.query(func.sum(Order.total_price)).join(Product).filter(
                Product.seller_id == current_user.id,
                func.strftime('%Y-%m-%d', Order.order_date) == day.isoformat(),
                Order.order_status == 'delivered'
            ).scalar() or 0
            week_values.append(float(day_total))
        month_labels, month_values = [], []
        for i in range(5, -1, -1):
            month = today.replace(day=1) - timedelta(days=i*30)
            month_labels.append(month.strftime('%b %Y'))
            start = month.replace(day=1)
            end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            month_total = db.session.query(func.sum(Order.total_price)).join(Product).filter(
                Product.seller_id == current_user.id,
                Order.order_date >= start, Order.order_date <= end,
                Order.order_status == 'delivered'
            ).scalar() or 0
            month_values.append(float(month_total))
        year_labels, year_values = [], []
        current_year = today.year
        for y in range(current_year-3, current_year+1):
            year_labels.append(str(y))
            year_total = db.session.query(func.sum(Order.total_price)).join(Product).filter(
                Product.seller_id == current_user.id,
                func.strftime('%Y', Order.order_date) == str(y),
                Order.order_status == 'delivered'
            ).scalar() or 0
            year_values.append(float(year_total))
        weekly_sales = {'labels': week_labels, 'values': week_values}
        monthly_sales = {'labels': month_labels, 'values': month_values}
        yearly_sales = {'labels': year_labels, 'values': year_values}
        available_balance = total_revenue
        pending_clearance = 0
        return render_template('seller_dashboard.html', my_products=my_products, total_products=total_products,
                               total_sold=total_sold, total_revenue=int(total_revenue), pending_orders=pending_orders,
                               recent_orders=recent, weekly_sales=weekly_sales, monthly_sales=monthly_sales,
                               yearly_sales=yearly_sales, available_balance=available_balance,
                               pending_clearance=pending_clearance)
    else:
        # Active orders include 'confirmed' (payment confirmed by callback)
        active_orders = Order.query.filter(
            Order.buyer_id == current_user.id,
            Order.order_status.in_(['pending', 'confirmed', 'shipped'])
        ).order_by(Order.order_date.desc()).all()
        recent_orders = Order.query.filter_by(buyer_id=current_user.id).order_by(Order.order_date.desc()).limit(5).all()
        # ✅ FIX: Total spent now includes all paid orders (not just delivered)
        total_spent = db.session.query(func.sum(Order.total_price)).filter(
            Order.buyer_id == current_user.id,
            Order.payment_status == 'paid'   # or use Order.order_status.in_(['confirmed','shipped','delivered'])
        ).scalar() or 0
        completed_orders_count = Order.query.filter_by(buyer_id=current_user.id, order_status='delivered').count()
        wishlist = Wishlist.query.filter_by(user_id=current_user.id).all()
        return render_template('buyer_dashboard.html', active_orders=active_orders, recent_orders=recent_orders,
                               total_spent=total_spent, completed_orders_count=completed_orders_count,
                               wishlist=wishlist)

# ========== BUYER PAGES ==========
@app.route('/my-purchases')
@login_required
def my_purchases():
    if current_user.role not in ['buyer', 'admin']:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    orders = Order.query.filter_by(buyer_id=current_user.id).order_by(Order.order_date.desc()).all()
    return render_template('my_purchases.html', orders=orders)

@app.route('/active-orders')
@login_required
def active_orders():
    if current_user.role not in ['buyer', 'admin']:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    active_orders = Order.query.filter(
        Order.buyer_id == current_user.id,
        Order.order_status.in_(['pending', 'confirmed', 'shipped'])
    ).order_by(Order.order_date.desc()).all()
    return render_template('active_orders.html', orders=active_orders)

@app.route('/wishlist')
@login_required
def wishlist():
    if current_user.role != 'buyer':
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    wishlist_items = Wishlist.query.filter_by(user_id=current_user.id).all()
    return render_template('wishlist.html', wishlist_items=wishlist_items)

@app.route('/add-to-wishlist/<int:product_id>')
@login_required
def add_to_wishlist(product_id):
    if current_user.role != 'buyer':
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    existing = Wishlist.query.filter_by(user_id=current_user.id, product_id=product_id).first()
    if not existing:
        new_wish = Wishlist(user_id=current_user.id, product_id=product_id)
        db.session.add(new_wish)
        db.session.commit()
        flash('Added to wishlist.', 'success')
    else:
        flash('Item already in wishlist.', 'info')
    return redirect(request.referrer or url_for('products'))

@app.route('/remove-from-wishlist/<int:product_id>')
@login_required
def remove_from_wishlist(product_id):
    Wishlist.query.filter_by(user_id=current_user.id, product_id=product_id).delete()
    db.session.commit()
    flash('Removed from wishlist.', 'success')
    return redirect(url_for('wishlist'))

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        if 'update_profile' in request.form:
            fullname = request.form.get('fullname')
            phone = request.form.get('phone')
            if fullname:
                current_user.fullname = fullname
            if phone:
                current_user.phone = phone
            db.session.commit()
            flash('Profile updated successfully.', 'success')
        elif 'change_password' in request.form:
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')
            if not check_password_hash(current_user.password, current_password):
                flash('Current password is incorrect.', 'danger')
                return redirect(url_for('settings'))
            if len(new_password) < 8:
                flash('New password must be at least 8 characters.', 'danger')
                return redirect(url_for('settings'))
            if new_password != confirm_password:
                flash('New passwords do not match.', 'danger')
                return redirect(url_for('settings'))
            current_user.password = generate_password_hash(new_password)
            db.session.commit()
            flash('Password changed successfully.', 'success')
        return redirect(url_for('settings'))
    return render_template('settings.html')

# ========== SELLER PAGES ==========
@app.route('/my-listings')
@login_required
def my_listings():
    if current_user.role != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    products = Product.query.filter_by(seller_id=current_user.id).all()
    return render_template('my_listings.html', products=products, active='listings')

@app.route('/my-orders')
@login_required
def my_orders():
    if current_user.role != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    orders = db.session.query(
        Order.id, Order.quantity, Order.total_price, Order.order_status, Order.order_date,
        Product.title.label('product_title'), User.fullname.label('buyer_name')
    ).join(Product, Order.product_id == Product.id
    ).join(User, Order.buyer_id == User.id
    ).filter(Product.seller_id == current_user.id
    ).order_by(Order.order_date.desc()).all()
    return render_template('my_orders.html', orders=orders, active='orders')

@app.route('/messages')
@login_required
def messages():
    if current_user.role != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    return render_template('messages.html', active='messages')

@app.route('/edit-product/<int:product_id>', methods=['GET', 'POST'])
@login_required
def edit_product(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        abort(404)
    if product.seller_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description', '')
        try:
            price = float(request.form.get('price', 0))
        except ValueError:
            price = product.price
        category = request.form.get('category', 'Uncategorized')
        condition = request.form.get('condition', 'good')
        if not title:
            flash('Product title is required.', 'danger')
            return redirect(request.url)
        product.title = title
        product.description = description
        product.price = price
        product.category = category
        product.condition = condition
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename:
                filename = secure_filename(f"{int(datetime.now().timestamp())}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                product.image_url = filename
        db.session.commit()
        flash('Product updated successfully.', 'success')
        return redirect(url_for('dashboard'))
    return render_template('edit_product.html', product=product, active='listings')

@app.route('/delete-product/<int:product_id>')
@login_required
def delete_product(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        abort(404)
    if product.seller_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('dashboard'))
    Order.query.filter_by(product_id=product.id).delete()
    db.session.delete(product)
    db.session.commit()
    flash('Product deleted successfully.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/update-order-status/<int:order_id>', methods=['POST'])
@login_required
def update_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    if order.product.seller_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('dashboard'))
    new_status = request.form.get('status')
    if new_status in ['pending', 'shipped', 'delivered', 'cancelled']:
        order.order_status = new_status
        db.session.commit()
        flash(f'Order #{order_id} status updated to {new_status}.', 'success')
    else:
        flash('Invalid status.', 'danger')
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/withdraw')
@login_required
def withdraw():
    if current_user.role != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    flash('Withdrawal feature under development. Your funds are safe.', 'info')
    return redirect(url_for('dashboard'))

# ---------- PRODUCT, CART, CHECKOUT ----------
@app.route('/add-product', methods=['GET', 'POST'])
@login_required
def add_product():
    if current_user.role != 'seller':
        flash('Only sellers can list products.', 'warning')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        price = float(request.form['price'])
        category = request.form['category']
        condition = request.form['condition']
        image = request.files['image']
        if image and image.filename:
            filename = secure_filename(f"{int(datetime.now().timestamp())}_{image.filename}")
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        else:
            filename = 'default.jpg'
        product = Product(
            seller_id=current_user.id, title=title, description=description, price=price,
            category=category, condition=condition, image_url=filename
        )
        db.session.add(product)
        db.session.commit()
        flash('Product listed successfully!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('add_product.html')

@app.route('/products')
def products():
    search = request.args.get('search', '')
    category = request.args.get('category', '')
    query = Product.query.filter_by(status='available')
    if search:
        query = query.filter(Product.title.contains(search) | Product.description.contains(search))
    if category:
        query = query.filter_by(category=category)
    all_products = query.all()
    return render_template('products.html', products=all_products, search=search, category=category)

@app.route('/product/<int:id>')
def product_detail(id):
    product = db.session.get(Product, id)
    if not product:
        abort(404)
    seller = db.session.get(User, product.seller_id)
    return render_template('product_detail.html', product=product, seller=seller)

@app.route('/add-to-cart/<int:product_id>')
@login_required
def add_to_cart(product_id):
    cart = session.get('cart', {})
    cart[str(product_id)] = cart.get(str(product_id), 0) + 1
    session['cart'] = cart
    flash('Item added to cart', 'success')
    return redirect(url_for('cart'))

@app.route('/cart')
@login_required
def cart():
    cart_items = []
    cart_data = session.get('cart', {})
    total = 0
    for product_id, qty in cart_data.items():
        product = db.session.get(Product, int(product_id))
        if product:
            cart_items.append({'product': product, 'quantity': qty})
            total += product.price * qty
    return render_template('cart.html', cart_items=cart_items, total=total)

@app.route('/checkout', methods=['GET'])
@login_required
def checkout_page():
    cart_data = session.get('cart', {})
    if not cart_data:
        flash('Your cart is empty.', 'warning')
        return redirect(url_for('products'))
    cart_items = []
    total = 0
    for product_id, qty in cart_data.items():
        product = db.session.get(Product, int(product_id))
        if product and product.status == 'available':
            cart_items.append({'product': product, 'quantity': qty, 'subtotal': product.price * qty})
            total += product.price * qty
    return render_template('checkout.html', cart_items=cart_items, total=total)

# ---------- MPESA INTEGRATION ----------
@app.route('/place-order', methods=['POST'])
@login_required
def place_order():
    cart_data = session.get('cart', {})
    if not cart_data:
        flash('Cart is empty', 'warning')
        return redirect(url_for('products'))

    phone_number = request.form.get('phone_number')
    if not phone_number:
        flash('Please enter your M-Pesa phone number.', 'danger')
        return redirect(url_for('checkout_page'))

    valid_items = []
    total_amount = 0
    for product_id, qty in cart_data.items():
        product = db.session.get(Product, int(product_id))
        if not product:
            flash(f'Product ID {product_id} no longer exists. Removed from cart.', 'warning')
            continue
        if product.status != 'available':
            flash(f'{product.title} is no longer available. Removed from cart.', 'warning')
            continue
        valid_items.append((product, qty))
        total_amount += product.price * qty

    if not valid_items:
        flash('Your cart contains no valid items. Please add new products.', 'danger')
        session['cart'] = {}
        return redirect(url_for('products'))

    session['cart'] = {}

    created_orders = []
    for product, qty in valid_items:
        order = Order(
            buyer_id=current_user.id,
            product_id=product.id,
            quantity=qty,
            total_price=product.price * qty,
            order_status='pending',
            payment_status='pending'
        )
        db.session.add(order)
        created_orders.append(order)
    db.session.commit()

    account_ref = f"Order-{created_orders[0].id}" if created_orders else "MultiOrder"
    payment_response = initiate_mpesa_payment(
        phone_number=phone_number,
        amount=total_amount,
        account_reference=account_ref,
        transaction_desc=f"Payment for {len(created_orders)} item(s)"
    )

    if payment_response and payment_response.get('ResponseCode') == '0':
        checkout_request_id = payment_response.get('CheckoutRequestID')
        for order in created_orders:
            order.checkout_request_id = checkout_request_id
        db.session.commit()
        time.sleep(2)
        return render_template('order_confirmation.html',
                               orders=created_orders,
                               total_amount=total_amount,
                               phone_number=phone_number)
    else:
        for order in created_orders:
            db.session.delete(order)
        db.session.commit()
        error_msg = payment_response.get('errorMessage', 'Failed to initiate payment. Please try again.')
        flash(error_msg, 'danger')
        return redirect(url_for('checkout_page'))

@app.route('/mpesa-callback', methods=['POST'])
def mpesa_callback():
    data = request.get_json()
    print("=== MPESA CALLBACK RECEIVED ===")
    print(json.dumps(data, indent=2))

    if data and data.get('Body', {}).get('stkCallback', {}).get('ResultCode') == 0:
        callback_data = data['Body']['stkCallback']
        checkout_request_id = callback_data['CheckoutRequestID']
        print(f"Payment successful for checkout ID: {checkout_request_id}")
        orders = Order.query.filter_by(checkout_request_id=checkout_request_id).all()
        if orders:
            for order in orders:
                order.payment_status = 'paid'
                order.order_status = 'confirmed'
                print(f"Updated order ID {order.id} to status 'confirmed'")
            metadata = callback_data.get('CallbackMetadata', {}).get('Item', [])
            receipt_number = None
            for item in metadata:
                if item.get('Name') == 'MpesaReceiptNumber':
                    receipt_number = item.get('Value')
                    break
            if receipt_number:
                for order in orders:
                    order.mpesa_receipt_number = receipt_number
                print(f"Receipt number: {receipt_number}")
            db.session.commit()
        else:
            print(f"No orders found for CheckoutRequestID {checkout_request_id}")
    else:
        print("Payment failed or callback data missing")
    return 'OK', 200

# ========== ADMIN MANAGEMENT ROUTES ==========
@app.route('/admin')
@login_required
def admin_dashboard():
    if current_user.role != 'admin' or current_user.email != 'elchapo7791@gmail.com':
        flash('Access denied. Admin only.', 'danger')
        return redirect(url_for('index'))

    total_users = User.query.count()
    total_products = Product.query.count()
    total_orders = Order.query.count()
    recent_orders = Order.query.order_by(Order.order_date.desc()).limit(10).all()
    all_users = User.query.order_by(User.id).all()
    all_products = Product.query.order_by(Product.id).all()

    return render_template('admin/dashboard.html',
                           total_users=total_users,
                           total_products=total_products,
                           total_orders=total_orders,
                           orders=recent_orders,
                           all_users=all_users,
                           all_products=all_products)

@app.route('/admin/change-role/<int:user_id>')
@login_required
def admin_change_role(user_id):
    if current_user.role != 'admin' or current_user.email != 'elchapo7791@gmail.com':
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    user = db.session.get(User, user_id)
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin_dashboard'))

    if user.role == 'buyer':
        user.role = 'seller'
    elif user.role == 'seller':
        user.role = 'admin'
    else:
        user.role = 'buyer'

    db.session.commit()
    flash(f'Role for {user.fullname} changed to {user.role}.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-user/<int:user_id>')
@login_required
def admin_delete_user(user_id):
    if current_user.role != 'admin' or current_user.email != 'elchapo7791@gmail.com':
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    user = db.session.get(User, user_id)
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin_dashboard'))

    if user.id == current_user.id:
        flash('You cannot delete your own admin account.', 'danger')
        return redirect(url_for('admin_dashboard'))

    Order.query.filter_by(buyer_id=user.id).delete()
    Order.query.join(Product).filter(Product.seller_id == user.id).delete()
    Product.query.filter_by(seller_id=user.id).delete()
    Wishlist.query.filter_by(user_id=user.id).delete()
    Message.query.filter((Message.sender_id == user.id) | (Message.receiver_id == user.id)).delete()

    db.session.delete(user)
    db.session.commit()
    flash(f'User {user.fullname} ({user.email}) deleted.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-product/<int:product_id>')
@login_required
def admin_delete_product(product_id):
    if current_user.role != 'admin' or current_user.email != 'elchapo7791@gmail.com':
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    product = db.session.get(Product, product_id)
    if not product:
        flash('Product not found.', 'danger')
        return redirect(url_for('admin_dashboard'))

    Order.query.filter_by(product_id=product.id).delete()
    Wishlist.query.filter_by(product_id=product.id).delete()
    db.session.delete(product)
    db.session.commit()
    flash(f'Product "{product.title}" deleted.', 'success')
    return redirect(url_for('admin_dashboard'))

# ---------- OTHER PAGES ----------
@app.route('/buyer-protection')
def buyer_protection():
    return render_template('buyer_protection.html')

@app.route('/help')
def help_centre():
    return render_template('help_centre.html')

# Print all registered routes at startup (for debugging)
print("=== Registered routes ===")
for rule in app.url_map.iter_rules():
    print(rule)

if __name__ == '__main__':
    app.run(debug=True)