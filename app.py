from flask import Flask, request, jsonify, send_from_directory, redirect
from flask_cors import CORS
import mysql.connector
from mysql.connector import Error
from datetime import datetime, timedelta
import logging
import os
import traceback

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ============================================
# DATABASE CONFIGURATION
# ============================================
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'shuttle.proxy.rlwy.net'),
    'port': int(os.environ.get('DB_PORT', 34003)),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', 'EdzIXocxkOmrjkmtCCdZZjZwOzZvafgm'),
    'database': os.environ.get('DB_NAME', 'railway')
}

# Test connection on startup
try:
    connection = mysql.connector.connect(**DB_CONFIG)
    if connection.is_connected():
        print("✅ Successfully connected to the database")
        cursor = connection.cursor()
        cursor.execute("SHOW TABLES LIKE 'queue_logs'")
        if not cursor.fetchone():
            print("⚠️ queue_logs table missing! Creating...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS queue_logs (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    token_number VARCHAR(20),
                    officer_id INT,
                    action VARCHAR(50),
                    action_details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_action_created (action, created_at)
                )
            """)
            connection.commit()
            print("✅ queue_logs table created")
        cursor.close()
    connection.close()
except Error as e:
    print(f"❌ Error while connecting to MySQL: {e}")


# ============================================
# SERVE HTML PAGES
# ============================================
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory('.', filename)


# ============================================
# DATABASE HELPER
# ============================================
def get_db_connection():
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except Exception as e:
        logger.error(f"[ERROR] Database connection error: {e}")
        raise e


# ============================================
# HEALTH CHECK
# ============================================
@app.route('/api/health', methods=['GET'])
def health_check():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        conn.close()
        return jsonify({'status': 'ok', 'database': 'connected', 'timestamp': datetime.now().isoformat()})
    except Exception as e:
        return jsonify({'status': 'ok', 'database': 'disconnected', 'error': str(e)})

# ============================================
# ADMIN OFFICE MANAGEMENT (CRUD)
# ============================================

@app.route('/api/admin/office', methods=['POST'])
def admin_create_office():
    """Create a new office"""
    data = request.get_json()
    
    office_code = data.get('office_code')
    office_name = data.get('office_name')
    location = data.get('location')
    description = data.get('description')
    
    if not office_code or not office_name:
        return jsonify({'success': False, 'message': 'Office code and name are required'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Check if office code already exists
        cursor.execute("SELECT id FROM offices WHERE office_code = %s", (office_code,))
        if cursor.fetchone():
            return jsonify({'success': False, 'message': f'Office code {office_code} already exists'}), 400
        
        # Get max display order
        cursor.execute("SELECT MAX(display_order) as max_order FROM offices")
        max_order = cursor.fetchone()
        display_order = (max_order['max_order'] or 0) + 1
        
        cursor.execute("""
            INSERT INTO offices (office_code, office_name, location, description, display_order, is_active)
            VALUES (%s, %s, %s, %s, %s, 1)
        """, (office_code, office_name, location, description, display_order))
        
        conn.commit()
        new_id = cursor.lastrowid
        
        return jsonify({
            'success': True, 
            'message': 'Office created successfully',
            'id': new_id
        })
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error creating office: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/admin/office/<int:office_id>', methods=['PUT'])
def admin_update_office(office_id):
    """Update an existing office"""
    data = request.get_json()
    
    office_code = data.get('office_code')
    office_name = data.get('office_name')
    location = data.get('location')
    description = data.get('description')
    is_active = data.get('is_active', 1)
    
    if not office_code or not office_name:
        return jsonify({'success': False, 'message': 'Office code and name are required'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Check if office exists
        cursor.execute("SELECT id FROM offices WHERE id = %s", (office_id,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'message': 'Office not found'}), 404
        
        # Check if another office has the same code
        cursor.execute("SELECT id FROM offices WHERE office_code = %s AND id != %s", (office_code, office_id))
        if cursor.fetchone():
            return jsonify({'success': False, 'message': f'Office code {office_code} already exists'}), 400
        
        cursor.execute("""
            UPDATE offices 
            SET office_code = %s, office_name = %s, location = %s, 
                description = %s, is_active = %s
            WHERE id = %s
        """, (office_code, office_name, location, description, is_active, office_id))
        
        conn.commit()
        
        return jsonify({'success': True, 'message': 'Office updated successfully'})
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error updating office: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/admin/office/<int:office_id>', methods=['DELETE'])
def admin_delete_office(office_id):
    """Delete an office and all associated data (services, officers, tokens)"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Check if office exists
        cursor.execute("SELECT id, office_code FROM offices WHERE id = %s", (office_id,))
        office = cursor.fetchone()
        if not office:
            return jsonify({'success': False, 'message': 'Office not found'}), 404
        
        # Delete all tokens for this office first (foreign key constraint)
        cursor.execute("DELETE FROM university_tokens WHERE office_id = %s", (office_id,))
        
        # Delete all queue logs for officers in this office
        cursor.execute("""
            DELETE FROM queue_logs 
            WHERE officer_id IN (SELECT id FROM officers WHERE office_id = %s)
        """, (office_id,))
        
        # Delete all office messages for this office
        cursor.execute("DELETE FROM office_messages WHERE office_id = %s", (office_id,))
        
        # Delete all services for this office
        cursor.execute("DELETE FROM services WHERE office_id = %s", (office_id,))
        
        # Delete all officers in this office
        cursor.execute("DELETE FROM officers WHERE office_id = %s", (office_id,))
        
        # Finally delete the office
        cursor.execute("DELETE FROM offices WHERE id = %s", (office_id,))
        
        conn.commit()
        
        return jsonify({'success': True, 'message': f'Office {office["office_code"]} deleted successfully'})
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error deleting office: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ============================================
# ADMIN SERVICE MANAGEMENT
# ============================================

@app.route('/api/admin/service', methods=['POST'])
def admin_create_service():
    """Create a new service under an office"""
    data = request.get_json()
    
    # ✅ CORRECT FIELD NAMES for service
    service_code = data.get('service_code')
    service_name = data.get('service_name')
    office_id = data.get('office_id')
    description = data.get('description')
    estimated_time_minutes = data.get('estimated_time_minutes', 5)
    display_order = data.get('display_order', 0)
    
    # ✅ Validate correct fields
    if not service_code or not service_name:
        return jsonify({'success': False, 'message': 'Service code and service name are required'}), 400
    
    if not office_id:
        return jsonify({'success': False, 'message': 'Office ID is required'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Check if office exists
        cursor.execute("SELECT id, office_name FROM offices WHERE id = %s", (office_id,))
        office = cursor.fetchone()
        if not office:
            return jsonify({'success': False, 'message': 'Office not found'}), 404
        
        # Check if service code already exists for this office
        cursor.execute("""
            SELECT id FROM services 
            WHERE service_code = %s AND office_id = %s
        """, (service_code, office_id))
        if cursor.fetchone():
            return jsonify({'success': False, 'message': f'Service code {service_code} already exists for this office'}), 400
        
        cursor.execute("""
            INSERT INTO services (service_code, service_name, office_id, description, estimated_time_minutes, display_order, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, 1)
        """, (service_code, service_name, office_id, description, estimated_time_minutes, display_order))
        
        conn.commit()
        new_id = cursor.lastrowid
        
        return jsonify({
            'success': True, 
            'message': f'Service {service_name} added to {office["office_name"]} successfully',
            'id': new_id
        })
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error creating service: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/admin/service/<int:service_id>', methods=['PUT'])
def admin_update_service(service_id):
    """Update an existing service"""
    data = request.get_json()
    
    service_code = data.get('service_code')
    service_name = data.get('service_name')
    description = data.get('description')
    estimated_time_minutes = data.get('estimated_time_minutes')
    is_active = data.get('is_active', 1)
    display_order = data.get('display_order', 0)
    
    # ✅ Validate correct fields
    if not service_code or not service_name:
        return jsonify({'success': False, 'message': 'Service code and service name are required'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT id FROM services WHERE id = %s", (service_id,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'message': 'Service not found'}), 404
        
        cursor.execute("""
            UPDATE services 
            SET service_code = %s, service_name = %s, description = %s,
                estimated_time_minutes = %s, is_active = %s, display_order = %s
            WHERE id = %s
        """, (service_code, service_name, description, estimated_time_minutes, is_active, display_order, service_id))
        
        conn.commit()
        
        return jsonify({'success': True, 'message': 'Service updated successfully'})
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error updating service: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()
@app.route('/api/admin/office/<int:office_id>/reset', methods=['POST'])
def admin_reset_office_queue(office_id):
    """Reset queue for a specific office - expires waiting tokens AND resets token counter to start from 01"""
    data = request.get_json() or {}
    officer_id = data.get('officer_id')
    is_admin = data.get('is_admin', False)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        # 1. AUTHORIZATION CHECK
        if not is_admin and officer_id:
            cursor.execute("SELECT office_id, is_admin FROM officers WHERE id=%s", (officer_id,))
            officer = cursor.fetchone()
            if not officer:
                return jsonify({'success': False, 'message': 'Officer not found'}), 404
            if not officer.get('is_admin') and officer['office_id'] != office_id:
                return jsonify({'success': False, 'message': 'Not authorised to reset this office queue'}), 403

        # 2. GET OFFICE DETAILS
        cursor.execute("SELECT id, office_code, office_name FROM offices WHERE id=%s", (office_id,))
        office = cursor.fetchone()
        
        if not office:
            return jsonify({'success': False, 'message': 'Office not found'}), 404
        
        # 3. EXPIRE ALL WAITING AND CALLED TOKENS
        cursor.execute("""
            UPDATE university_tokens
            SET status = 'expired'
            WHERE office_id = %s AND status IN ('waiting', 'called')
        """, (office_id,))
        
        # 4. 🔥 RESET TOKEN COUNTER - Delete today's expired/skipped tokens for this office
        # This ensures next token starts from 01 again
        cursor.execute("""
            DELETE FROM university_tokens
            WHERE office_id = %s 
            AND DATE(requested_at) = CURDATE()
            AND status IN ('expired', 'skipped')
        """, (office_id,))
        
        # 5. LOG THE RESET ACTION
        cursor.execute("""
            INSERT INTO queue_logs (token_number, officer_id, action, action_details, created_at)
            VALUES ('SYSTEM', %s, 'queue_reset', 
                    CONCAT('Queue reset for ', %s, ' - Counter reset. Next token will be ', %s, '01'), NOW())
        """, (officer_id, office['office_name'], office['office_code']))
        
        conn.commit()
        
        return jsonify({
            'success': True, 
            'message': f'Queue reset for {office["office_name"]}. Next token will be {office["office_code"]}01',
            'next_token': f'{office["office_code"]}01'
        })
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error resetting office queue: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()
        
@app.route('/api/admin/service/<int:service_id>', methods=['DELETE'])
def admin_delete_service(service_id):
    """Delete a service"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT id FROM services WHERE id = %s", (service_id,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'message': 'Service not found'}), 404
        
        cursor.execute("DELETE FROM services WHERE id = %s", (service_id,))
        conn.commit()
        
        return jsonify({'success': True, 'message': 'Service deleted successfully'})
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error deleting service: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ============================================
# ADMIN OFFICER MANAGEMENT (Enhanced)
# ============================================

@app.route('/api/admin/officer', methods=['POST'])
def admin_create_officer():
    """Create a new officer and assign to office"""
    data = request.get_json()
    
    officer_number = data.get('officer_number')
    officer_name = data.get('officer_name')
    email = data.get('email')
    phone = data.get('phone')
    office_id = data.get('office_id')
    pin_code = data.get('pin_code', '1234')
    
    if not officer_number or not officer_name or not office_id:
        return jsonify({'success': False, 'message': 'Officer number, name, and office_id are required'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Check if office exists
        cursor.execute("SELECT id FROM offices WHERE id = %s", (office_id,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'message': 'Office not found'}), 404
        
        # Check if officer number already exists
        cursor.execute("SELECT id FROM officers WHERE officer_number = %s", (officer_number,))
        if cursor.fetchone():
            return jsonify({'success': False, 'message': f'Officer number {officer_number} already exists'}), 400
        
        cursor.execute("""
            INSERT INTO officers (officer_number, officer_name, email, phone, office_id, pin_code, status, is_admin)
            VALUES (%s, %s, %s, %s, %s, %s, 'available', 0)
        """, (officer_number, officer_name, email, phone, office_id, pin_code))
        
        conn.commit()
        new_id = cursor.lastrowid
        
        return jsonify({
            'success': True, 
            'message': 'Officer created successfully',
            'id': new_id
        })
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error creating officer: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/admin/officer/<int:officer_id>', methods=['PUT'])
def admin_update_officer(officer_id):
    """Update an existing officer"""
    data = request.get_json()
    
    officer_number = data.get('officer_number')
    officer_name = data.get('officer_name')
    email = data.get('email')
    phone = data.get('phone')
    office_id = data.get('office_id')
    pin_code = data.get('pin_code')
    status = data.get('status')
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT id FROM officers WHERE id = %s", (officer_id,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'message': 'Officer not found'}), 404
        
        # Build update query dynamically
        update_fields = []
        params = []
        
        if officer_number:
            update_fields.append("officer_number = %s")
            params.append(officer_number)
        if officer_name:
            update_fields.append("officer_name = %s")
            params.append(officer_name)
        if email is not None:
            update_fields.append("email = %s")
            params.append(email)
        if phone is not None:
            update_fields.append("phone = %s")
            params.append(phone)
        if office_id:
            update_fields.append("office_id = %s")
            params.append(office_id)
        if pin_code:
            update_fields.append("pin_code = %s")
            params.append(pin_code)
        if status:
            update_fields.append("status = %s")
            params.append(status)
        
        if update_fields:
            params.append(officer_id)
            query = f"UPDATE officers SET {', '.join(update_fields)} WHERE id = %s"
            cursor.execute(query, params)
        
        conn.commit()
        
        return jsonify({'success': True, 'message': 'Officer updated successfully'})
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error updating officer: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/admin/officer/<int:officer_id>', methods=['DELETE'])
def admin_delete_officer(officer_id):
    """Delete an officer"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT id FROM officers WHERE id = %s", (officer_id,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'message': 'Officer not found'}), 404
        
        # Delete queue logs for this officer
        cursor.execute("DELETE FROM queue_logs WHERE officer_id = %s", (officer_id,))
        
        # Delete office messages by this officer
        cursor.execute("DELETE FROM office_messages WHERE officer_id = %s", (officer_id,))
        
        # Update tokens to remove officer assignment
        cursor.execute("UPDATE university_tokens SET assigned_officer_id = NULL WHERE assigned_officer_id = %s", (officer_id,))
        
        # Delete the officer
        cursor.execute("DELETE FROM officers WHERE id = %s", (officer_id,))
        
        conn.commit()
        
        return jsonify({'success': True, 'message': 'Officer deleted successfully'})
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error deleting officer: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ============================================
# ADMIN OFFICE TOGGLE ACTIVE STATUS
# ============================================

@app.route('/api/admin/office/<int:office_id>/toggle', methods=['POST'])
def admin_toggle_office_active(office_id):
    """Toggle office active status"""
    data = request.get_json()
    is_active = data.get('is_active')
    
    if is_active is None:
        return jsonify({'success': False, 'message': 'is_active field required'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("UPDATE offices SET is_active = %s WHERE id = %s", (is_active, office_id))
        conn.commit()
        
        status_text = "activated" if is_active else "deactivated"
        return jsonify({'success': True, 'message': f'Office {status_text} successfully'})
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error toggling office: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ============================================
# GET ALL OFFICES WITH DETAILS (For Student Kiosk)
# ============================================

@app.route('/api/offices/all', methods=['GET'])
def get_all_offices_with_services():
    """Get all offices with their services for the kiosk"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT id, office_code, office_name, description, location, is_active, display_order
            FROM offices
            WHERE is_active = 1
            ORDER BY display_order
        """)
        offices = cursor.fetchall()
        
        for office in offices:
            cursor.execute("""
                SELECT id, service_code, service_name, description, estimated_time_minutes
                FROM services
                WHERE office_id = %s AND is_active = 1
                ORDER BY display_order
            """, (office['id'],))
            office['services'] = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return jsonify({'success': True, 'offices': offices})
        
    except Exception as e:
        logger.error(f"Error getting offices with services: {e}")
        return jsonify({'success': False, 'message': str(e)})


# ============================================
# UPDATE OFFICE DISPLAY ORDER
# ============================================

@app.route('/api/admin/office/reorder', methods=['POST'])
def admin_reorder_offices():
    """Update display order of offices"""
    data = request.get_json()
    orders = data.get('orders', [])
    
    if not orders:
        return jsonify({'success': False, 'message': 'No order data provided'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        for item in orders:
            cursor.execute("UPDATE offices SET display_order = %s WHERE id = %s", (item['order'], item['id']))
        
        conn.commit()
        return jsonify({'success': True, 'message': 'Office order updated successfully'})
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error reordering offices: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ============================================
# OFFICE AND SERVICES ENDPOINTS
# ============================================
@app.route('/api/offices', methods=['GET'])
def get_offices():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, office_code, office_name, description, location, is_active, display_order
            FROM offices
            WHERE is_active = 1
            ORDER BY display_order
        """)
        offices = cursor.fetchall()
        cursor.close()
        conn.close()
        return jsonify({'success': True, 'offices': offices})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/offices/<int:office_id>/services', methods=['GET'])
def get_office_services(office_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, service_code, service_name, description, 
                   estimated_time_minutes, is_active, display_order
            FROM services
            WHERE office_id = %s AND is_active = 1
            ORDER BY display_order
        """, (office_id,))
        services = cursor.fetchall()
        cursor.close()
        conn.close()
        return jsonify({'success': True, 'services': services})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# ============================================
# STUDENT TOKEN GENERATION
# ============================================
@app.route('/api/student/token', methods=['POST'])
def generate_student_token():
    data = request.get_json()

    office_id = data.get('office_id')
    service_id = data.get('service_id')
    service_code = data.get('service_code')
    student_name = data.get('student_name')
    student_id = data.get('student_id')
    student_phone = data.get('student_phone')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # -------------------------
        # OFFICE VALIDATION
        # -------------------------
        cursor.execute("""
            SELECT id, office_code, office_name, location 
            FROM offices 
            WHERE id = %s AND is_active = 1
        """, (office_id,))
        office = cursor.fetchone()

        if not office:
            return jsonify({'success': False, 'message': 'Office not available'}), 400

        # -------------------------
        # SERVICE VALIDATION
        # -------------------------
        cursor.execute("""
            SELECT id, service_name, estimated_time_minutes 
            FROM services 
            WHERE id = %s AND is_active = 1
        """, (service_id,))
        service = cursor.fetchone()

        if not service:
            return jsonify({'success': False, 'message': 'Service not available'}), 400

        # -------------------------
        # OFFICER CHECK
        # -------------------------
        cursor.execute("""
            SELECT COUNT(*) as cnt 
            FROM officers
            WHERE office_id = %s AND status != 'offline'
        """, (office_id,))
        officer_check = cursor.fetchone()

        if not officer_check or officer_check['cnt'] == 0:
            return jsonify({
                'success': False,
                'message': 'No officers available for this office right now'
            }), 400

        # -------------------------
        # GET NEXT TOKEN NUMBER (SAFE)
        # -------------------------
        cursor.execute("""
            SELECT MAX(
                CAST(SUBSTRING(token_number, LENGTH(%s) + 1) AS UNSIGNED)
            ) AS max_num
            FROM university_tokens
            WHERE office_id = %s
        """, (office['office_code'], office_id))

        result = cursor.fetchone()
        max_number = result['max_num'] or 0

        next_num = max_number + 1
        token_number = f"{office['office_code']}{str(next_num).zfill(2)}"

        print(f"📊 Token generated: {token_number} (max={max_number})")

        # -------------------------
        # QUEUE POSITION (FIXED)
        # -------------------------
        cursor.execute("""
            SELECT COUNT(*) as ahead_count
            FROM university_tokens
            WHERE office_id = %s AND status = 'waiting'
        """, (office_id,))

        ahead = cursor.fetchone()
        ahead_count = ahead['ahead_count'] if ahead else 0

        queue_position = ahead_count + 1
        estimated_wait = ahead_count * service['estimated_time_minutes']

        # -------------------------
        # INSERT TOKEN (WITH SAFETY)
        # -------------------------
        cursor.execute("""
            INSERT INTO university_tokens
                (token_number, office_id, service_id, service_code,
                 student_name, student_id, student_phone,
                 status, queue_position, estimated_wait_minutes, source, requested_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s,
                    'waiting', %s, %s, 'kiosk', NOW())
        """, (
            token_number,
            office_id,
            service_id,
            service_code,
            student_name,
            student_id,
            student_phone,
            queue_position,
            estimated_wait
        ))

        conn.commit()

        return jsonify({
            'success': True,
            'token_number': token_number,
            'office_name': office['office_name'],
            'service_name': service['service_name'],
            'location': office.get('location', 'Main Campus'),
            'queue_position': queue_position,
            'ahead_count': ahead_count,
            'estimated_wait': estimated_wait
        })

    except Exception as e:
        conn.rollback()
        logger.error(f"Token generation error: {e}")
        logger.error(traceback.format_exc())

        return jsonify({
            'success': False,
            'message': 'Internal server error'
        }), 500

    finally:
        cursor.close()
        conn.close()

# ============================================
# OFFICER LOGIN
# ============================================
@app.route('/api/officer/login', methods=['POST'])
def officer_login():
    data = request.get_json()
    officer_number = data.get('officer_number')
    pin_code = data.get('pin_code')

    if not officer_number or not pin_code:
        return jsonify({'success': False, 'message': 'Officer number and PIN required'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT o.id, o.officer_number, o.officer_name, o.office_id,
                   o.status, o.is_admin,
                   off.office_code, off.office_name, off.location
            FROM officers o
            JOIN offices off ON o.office_id = off.id
            WHERE o.officer_number = %s AND o.pin_code = %s
        """, (officer_number, pin_code))

        officer = cursor.fetchone()

        if not officer:
            return jsonify({'success': False, 'message': 'Invalid number or PIN'}), 401

        role = 'admin' if officer.get('is_admin') else 'officer'

        return jsonify({
            'success': True,
            'user': {
                'id': officer['id'],
                'officer_number': officer['officer_number'],
                'officer_name': officer['officer_name'],
                'office_id': officer['office_id'],
                'office_code': officer['office_code'],
                'office_name': officer['office_name'],
                'location': officer.get('location', ''),
                'status': officer['status'],
                'role': role,
                'user_type': role
            }
        })

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ============================================
# OFFICER QUEUE
# ============================================
@app.route('/api/officer/queue/<int:officer_id>', methods=['GET'])
def get_officer_queue(officer_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT o.id, o.officer_name, o.office_id, o.status, o.current_token,
                   off.office_code, off.office_name, off.location
            FROM officers o
            JOIN offices off ON o.office_id = off.id
            WHERE o.id = %s
        """, (officer_id,))
        officer = cursor.fetchone()
        if not officer:
            return jsonify({'success': False, 'message': 'Officer not found'})

        cursor.execute("""
            SELECT t.id, t.token_number, t.student_name, t.student_id, t.student_phone,
                   t.service_code, t.requested_at,
                   s.service_name,
                   TIMESTAMPDIFF(MINUTE, t.requested_at, NOW()) as waiting_minutes
            FROM university_tokens t
            LEFT JOIN services s ON t.service_id = s.id
            WHERE t.office_id = %s AND t.status = 'waiting'
            ORDER BY t.requested_at ASC
        """, (officer['office_id'],))
        waiting = cursor.fetchall()

        cursor.execute("""
            SELECT t.token_number, t.status, t.called_at, t.serving_started_at,
                   t.service_code, s.service_name
            FROM university_tokens t
            LEFT JOIN services s ON t.service_id = s.id
            WHERE t.office_id = %s AND t.status IN ('called','serving')
            ORDER BY t.called_at DESC LIMIT 1
        """, (officer['office_id'],))
        current = cursor.fetchone()

        cursor.execute("""
            SELECT COUNT(*) as cnt FROM university_tokens
            WHERE office_id = %s
              AND status = 'completed'
              AND DATE(completed_at) = CURDATE()
        """, (officer['office_id'],))
        completed_row = cursor.fetchone()
        completed_today = completed_row['cnt'] if completed_row else 0

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'waiting': waiting,
            'current': current,
            'office_code': officer['office_code'],
            'office_name': officer['office_name'],
            'location': officer.get('location', ''),
            'completed_today': completed_today
        })

    except Exception as e:
        logger.error(f"Error in get_officer_queue: {e}")
        return jsonify({'success': False, 'message': str(e)})


# ============================================
# PUBLIC QUEUES
# ============================================
@app.route('/api/public/queues', methods=['GET'])
def get_public_queues():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT id, office_code, office_name, location
            FROM offices
            WHERE is_active = 1
            ORDER BY display_order
        """)
        offices = cursor.fetchall()

        result = []
        for office in offices:
            # Get current called token with student name
            cursor.execute("""
                SELECT t.token_number, t.student_name
                FROM university_tokens t
                WHERE t.office_id = %s AND t.status = 'called'
                ORDER BY t.called_at DESC LIMIT 1
            """, (office['id'],))
            called = cursor.fetchone()

            # Get current serving token with student name
            cursor.execute("""
                SELECT t.token_number, t.student_name
                FROM university_tokens t
                WHERE t.office_id = %s AND t.status = 'serving'
                ORDER BY t.serving_started_at DESC LIMIT 1
            """, (office['id'],))
            serving = cursor.fetchone()

            # Get waiting count
            cursor.execute("""
                SELECT COUNT(*) as waiting_count FROM university_tokens
                WHERE office_id = %s AND status = 'waiting'
            """, (office['id'],))
            waiting_count = cursor.fetchone()

            result.append({
                'office_id': office['id'],
                'office_code': office['office_code'],
                'office_name': office['office_name'],
                'location': office.get('location', ''),
                'current_called': called['token_number'] if called else None,
                'called_student': called['student_name'] if called else None,
                'current_serving': serving['token_number'] if serving else None,
                'serving_student': serving['student_name'] if serving else None,
                'waiting_count': waiting_count['waiting_count'] if waiting_count else 0
            })

        cursor.close()
        conn.close()
        return jsonify({'success': True, 'queues': result})

    except Exception as e:
        logger.error(f"Error in get_public_queues: {e}")
        return jsonify({'success': False, 'message': str(e)})

# ============================================
# OFFICER ACTIONS - WITH RECALL LOGGING

# ============================================
@app.route('/api/officer/call-next', methods=['POST'])
def officer_call_next():
    data = request.get_json()
    officer_id = data.get('officer_id')
    officer_number = data.get('officer_number')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT office_id FROM officers WHERE id=%s", (officer_id,))
        officer = cursor.fetchone()
        if not officer:
            return jsonify({'success': False, 'message': 'Officer not found'})

        # Auto-complete any currently serving student
        cursor.execute("""
            SELECT token_number FROM university_tokens
            WHERE office_id = %s AND status = 'serving'
        """, (officer['office_id'],))
        current_serving = cursor.fetchone()
        
        if current_serving:
            cursor.execute("""
                UPDATE university_tokens
                SET status = 'completed', completed_at = NOW()
                WHERE token_number = %s
            """, (current_serving['token_number'],))
            print(f"[OK] Auto-completed previous token: {current_serving['token_number']}")

        # Get next waiting token with student name
        cursor.execute("""
            SELECT id, token_number, student_name, service_code 
            FROM university_tokens
            WHERE office_id=%s AND status='waiting'
            ORDER BY requested_at ASC LIMIT 1
        """, (officer['office_id'],))
        
        token = cursor.fetchone()
        if not token:
            return jsonify({'success': False, 'message': 'No students waiting'})

        cursor.execute("""
            UPDATE university_tokens
            SET status='called', called_at=NOW(),
                assigned_officer_id=%s, assigned_officer_number=%s
            WHERE id=%s
        """, (officer_id, officer_number, token['id']))

        cursor.execute("""
            UPDATE officers SET status='called', current_token=%s, last_activity=NOW()
            WHERE id=%s
        """, (token['token_number'], officer_id))

        # Insert recall log with student name for voice announcement
        cursor.execute("""
            INSERT INTO queue_logs (token_number, officer_id, action, action_details, created_at)
            VALUES (%s, %s, 'recall', 'Called from officer dashboard', NOW())
        """, (token['token_number'], officer_id))

        conn.commit()
        print(f"[INFO] RECORDED RECALL: {token['token_number']} for student: {token['student_name']}")

        # Return student name for voice announcement on public display
        return jsonify({
            'success': True, 
            'token_number': token['token_number'], 
            'student_name': token['student_name'] or '', 
            'service_code': token['service_code']
        })

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Error in call-next: {e}")
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/officer/call-specific', methods=['POST'])
def officer_call_specific():
    data = request.get_json()
    officer_id = data.get('officer_id')
    officer_number = data.get('officer_number')
    token_number = data.get('token_number')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            UPDATE university_tokens
            SET status='called', called_at=NOW(),
                assigned_officer_id=%s, assigned_officer_number=%s
            WHERE token_number=%s AND status='waiting'
        """, (officer_id, officer_number, token_number))

        cursor.execute("""
            UPDATE officers SET status='called', current_token=%s, last_activity=NOW()
            WHERE id=%s
        """, (token_number, officer_id))

        # Insert recall log for public display
        cursor.execute("""
            INSERT INTO queue_logs (token_number, officer_id, action, action_details, created_at)
            VALUES (%s, %s, 'recall', 'Called from officer dashboard', NOW())
        """, (token_number, officer_id))

        conn.commit()
        print(f"[INFO] RECORDED RECALL: {token_number}")

        return jsonify({'success': True, 'token_number': token_number})
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Error in call-specific: {e}")
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/officer/serve', methods=['POST'])
def officer_serve():
    data = request.get_json()
    officer_id = data.get('officer_id')
    token_number = data.get('token_number')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            UPDATE university_tokens 
            SET status='serving', serving_started_at=NOW() 
            WHERE token_number=%s
        """, (token_number,))
        cursor.execute("""
            UPDATE officers SET status='busy', last_activity=NOW() 
            WHERE id=%s
        """, (officer_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/officer/complete', methods=['POST'])
def officer_complete():
    data = request.get_json()
    officer_id = data.get('officer_id')
    token_number = data.get('token_number')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            UPDATE university_tokens 
            SET status='completed', completed_at=NOW() 
            WHERE token_number=%s
        """, (token_number,))
        cursor.execute("""
            UPDATE officers SET status='available', current_token=NULL, last_activity=NOW() 
            WHERE id=%s
        """, (officer_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/officer/skip', methods=['POST'])
def officer_skip():
    data = request.get_json()
    officer_id = data.get('officer_id')
    token_number = data.get('token_number')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            UPDATE university_tokens 
            SET status='skipped', skipped_at=NOW() 
            WHERE token_number=%s
        """, (token_number,))
        cursor.execute("""
            UPDATE officers SET status='available', current_token=NULL, last_activity=NOW() 
            WHERE id=%s
        """, (officer_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/officer/recall', methods=['POST'])
def officer_recall():
    data = request.get_json()
    officer_id = data.get('officer_id')
    token_number = data.get('token_number')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            INSERT INTO queue_logs (token_number, officer_id, action, action_details, created_at)
            VALUES (%s, %s, 'recall', 'Manual recall announcement', NOW())
        """, (token_number, officer_id))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ============================================
# OFFICE MESSAGES
# ============================================
@app.route('/api/office/message', methods=['POST'])
def post_office_message():
    data = request.get_json()
    office_id = data.get('office_id')
    message = data.get('message')
    message_type = data.get('message_type', 'info')
    officer_id = data.get('officer_id')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("UPDATE office_messages SET is_active=0 WHERE office_id=%s", (office_id,))
        cursor.execute("""
            INSERT INTO office_messages (office_id, message, message_type, officer_id, is_active, created_at)
            VALUES (%s, %s, %s, %s, 1, NOW())
        """, (office_id, message, message_type, officer_id))
        conn.commit()
        return jsonify({'success': True, 'message': 'Message posted'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/office/messages', methods=['GET'])
def get_office_messages():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        office_id = request.args.get('office_id', type=int)
        office_code = request.args.get('office_code', type=str)
        include_inactive = request.args.get('include_inactive', default='0')
        limit = request.args.get('limit', default=50, type=int)

        # Clamp limit to avoid unbounded response size
        if limit is None:
            limit = 50
        limit = max(1, min(limit, 200))

        where_clauses = []
        params = []

        if include_inactive != '1':
            where_clauses.append("om.is_active = 1")

        if office_id:
            where_clauses.append("om.office_id = %s")
            params.append(office_id)
        elif office_code:
            where_clauses.append("off.office_code = %s")
            params.append(office_code)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        query = f"""
            SELECT om.id, om.office_id, om.message, om.message_type, om.created_at, 
                   off.office_name, off.office_code
            FROM office_messages om
            JOIN offices off ON om.office_id = off.id
            {where_sql}
            ORDER BY om.created_at DESC
            LIMIT %s
        """
        params.append(limit)
        cursor.execute(query, tuple(params))
        messages = cursor.fetchall()
        for m in messages:
            if isinstance(m.get('created_at'), datetime):
                m['created_at'] = m['created_at'].isoformat()
        cursor.close()
        conn.close()
        return jsonify({'success': True, 'messages': messages})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/officer/messages/<int:officer_id>', methods=['GET'])
def get_officer_messages(officer_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, office_id, message, message_type, created_at, is_active
            FROM office_messages
            WHERE officer_id = %s
            ORDER BY created_at DESC
        """, (officer_id,))
        messages = cursor.fetchall()
        for m in messages:
            if isinstance(m.get('created_at'), datetime):
                m['created_at'] = m['created_at'].isoformat()
        cursor.close()
        conn.close()
        return jsonify({'success': True, 'messages': messages})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/office/message/<int:message_id>', methods=['DELETE'])
def delete_office_message(message_id):
    data = request.get_json()
    officer_id = data.get('officer_id')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT officer_id FROM office_messages WHERE id=%s", (message_id,))
        msg = cursor.fetchone()
        if not msg:
            return jsonify({'success': False, 'message': 'Message not found'}), 404
        if msg['officer_id'] != officer_id:
            return jsonify({'success': False, 'message': 'You can only delete your own messages'}), 403
        cursor.execute("DELETE FROM office_messages WHERE id=%s", (message_id,))
        conn.commit()
        return jsonify({'success': True, 'message': 'Message deleted'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ============================================
# ADMIN STATS
# ============================================
@app.route('/api/admin/stats', methods=['GET'])
def admin_get_stats():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # 🔥 FIX: Remove date filter to count ALL waiting tokens (like public display)
        cursor.execute("""
            SELECT 
                off.id, off.office_code, off.office_name, off.location, off.is_active,
                COUNT(CASE WHEN t.status = 'waiting' THEN 1 END) as waiting,
                COUNT(CASE WHEN t.status = 'called' THEN 1 END) as called,
                COUNT(CASE WHEN t.status = 'serving' THEN 1 END) as serving,
                COUNT(CASE WHEN t.status = 'completed' THEN 1 END) as completed,
                COUNT(CASE WHEN t.status = 'skipped' THEN 1 END) as skipped
            FROM offices off
            LEFT JOIN university_tokens t ON off.id = t.office_id
            GROUP BY off.id
            ORDER BY off.display_order
        """)
        stats = cursor.fetchall()

        cursor.execute("""
            SELECT o.id, o.officer_number, o.officer_name, o.status, o.current_token,
                   o.email, o.phone, o.office_id,
                   off.office_name, off.office_code
            FROM officers o
            JOIN offices off ON o.office_id = off.id
            WHERE o.is_admin = 0 OR o.is_admin IS NULL
            ORDER BY off.display_order, o.officer_number
        """)
        officers = cursor.fetchall()

        cursor.close()
        conn.close()
        return jsonify({'success': True, 'stats': stats, 'officers': officers})
    except Exception as e:
        logger.error(f"Error in admin_get_stats: {e}")
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/admin/daily-stats', methods=['GET'])
def admin_daily_stats():
    try:
        target_date = request.args.get('date')

        if not target_date:
            target_date = datetime.now().strftime('%Y-%m-%d')

        start = f"{target_date} 00:00:00"
        end = f"{target_date} 23:59:59"

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                off.id,
                off.office_code,
                off.office_name,
                off.location,

                -- DAILY ISSUES
                COUNT(CASE WHEN t.requested_at BETWEEN %s AND %s THEN 1 END) AS total_tokens,

                -- DAILY PROGRESSION
                COUNT(CASE WHEN t.called_at BETWEEN %s AND %s THEN 1 END) AS tokens_called,
                COUNT(CASE WHEN t.serving_started_at BETWEEN %s AND %s THEN 1 END) AS service_started_count,
                COUNT(CASE WHEN t.completed_at BETWEEN %s AND %s THEN 1 END) AS completed,
                COUNT(CASE WHEN t.skipped_at BETWEEN %s AND %s THEN 1 END) AS skipped,

                -- LIVE QUEUE (CURRENT STATE ONLY)
                COUNT(CASE WHEN t.status = 'waiting' THEN 1 END) AS current_waiting,
                COUNT(CASE WHEN t.status = 'serving' THEN 1 END) AS currently_serving,

                -- PERFORMANCE METRICS (ONLY COMPLETED TODAY TOKENS)
                ROUND(AVG(
                    CASE
                        WHEN t.completed_at BETWEEN %s AND %s
                        THEN TIMESTAMPDIFF(MINUTE, t.requested_at, t.completed_at)
                    END
                ), 1) AS avg_turnaround_minutes,

                ROUND(AVG(
                    CASE
                        WHEN t.completed_at BETWEEN %s AND %s
                        THEN TIMESTAMPDIFF(MINUTE, t.serving_started_at, t.completed_at)
                    END
                ), 1) AS avg_service_minutes,

                ROUND(AVG(
                    CASE
                        WHEN t.serving_started_at BETWEEN %s AND %s
                        THEN TIMESTAMPDIFF(MINUTE, t.requested_at, t.serving_started_at)
                    END
                ), 1) AS avg_queue_wait_before_service_minutes,

                ROUND(AVG(
                    CASE
                        WHEN t.called_at BETWEEN %s AND %s
                        THEN TIMESTAMPDIFF(MINUTE, t.called_at, t.serving_started_at)
                    END
                ), 1) AS avg_response_after_call_minutes

            FROM offices off
            LEFT JOIN university_tokens t ON off.id = t.office_id
            WHERE off.is_active = 1
            GROUP BY off.id
            ORDER BY off.display_order
        """, (
            start, end,  # requested
            start, end,  # called
            start, end,  # serving started
            start, end,  # completed
            start, end,  # skipped

            start, end,  # avg turnaround
            start, end,  # avg service
            start, end,  # avg wait before service
            start, end   # avg after call
        ))

        offices = cursor.fetchall()

        # completion rate (based only on closed tokens today)
        for row in offices:
            completed = row.get('completed') or 0
            skipped = row.get('skipped') or 0
            closed = completed + skipped

            row['completion_rate'] = round((completed / closed) * 100, 1) if closed > 0 else 0

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'date': target_date,
            'offices': offices
        })

    except Exception as e:
        logger.error(f"Error in admin_daily_stats: {e}")
        return jsonify({'success': False, 'message': str(e)})

# ============================================
# RUN APPLICATION
# ============================================
if __name__ == '__main__':
    PORT = int(os.environ.get('PORT', 8080))
    print("=" * 55)
    print("MAKERERE UNIVERSITY QUEUE SYSTEM API")
    print("=" * 55)
    print(f"http://localhost:{PORT}")
    print()
    print("Office Hierarchy Enabled:")
    print("  - Academic Registrar Office (AR) -> Registry, Testimonials, General")
    print("  - Records Office (REC) -> Admission Letters, Year One Registration, Transcripts")
    print()
    print("Features:")
    print("  - Student token generation with office + service selection")
    print("  - Officer dashboard with service-aware queue")
    print("  - Public display with real-time called tokens")
    print("  - Voice announcements for called/serving tokens")
    print("  - Recall logging for public display synchronization")
    print("=" * 55)
    app.run(host='0.0.0.0', port=PORT, debug=True)
