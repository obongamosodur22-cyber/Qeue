-- ============================================
-- UNIVERSITY REGISTRY QUEUE SYSTEM
-- ============================================

-- Drop existing tables if re-running
DROP TABLE IF EXISTS service_offices;
DROP TABLE IF EXISTS officers;
DROP TABLE IF EXISTS university_tokens;
DROP TABLE IF EXISTS university_queue_logs;
DROP TABLE IF EXISTS daily_stats;

-- ============================================
-- 1. SERVICE OFFICES (Registry, Transcripts, Testimonials, General Inquiry)
-- ============================================
CREATE TABLE service_offices (
    id INT PRIMARY KEY AUTO_INCREMENT,
    office_code VARCHAR(20) UNIQUE NOT NULL,
    office_name VARCHAR(100) NOT NULL,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    display_order INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- 2. OFFICERS (Staff assigned to offices)
-- ============================================
CREATE TABLE officers (
    id INT PRIMARY KEY AUTO_INCREMENT,
    officer_number INT UNIQUE NOT NULL,
    officer_name VARCHAR(100) NOT NULL,
    email VARCHAR(100),
    phone VARCHAR(20),
    pin_code VARCHAR(100) DEFAULT '1234',
    office_id INT,
    status VARCHAR(20) DEFAULT 'available', -- available, busy, called, offline
    current_token VARCHAR(20),
    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (office_id) REFERENCES service_offices(id)
);

-- ============================================
-- 3. UNIVERSITY QUEUE TOKENS
-- ============================================
CREATE TABLE university_tokens (
    id INT PRIMARY KEY AUTO_INCREMENT,
    token_number VARCHAR(20) UNIQUE NOT NULL,
    office_code VARCHAR(20) NOT NULL,
    student_name VARCHAR(100),
    student_id VARCHAR(50),
    student_phone VARCHAR(20),
    status VARCHAR(20) DEFAULT 'waiting',
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    called_at TIMESTAMP NULL,
    serving_started_at TIMESTAMP NULL,
    completed_at TIMESTAMP NULL,
    skipped_at TIMESTAMP NULL,
    assigned_officer_id INT,
    assigned_officer_number INT,
    queue_position INT,
    estimated_wait_minutes INT,
    call_attempts INT DEFAULT 0,
    source VARCHAR(20) DEFAULT 'kiosk',
    FOREIGN KEY (assigned_officer_id) REFERENCES officers(id)
);

-- ============================================
-- 4. QUEUE LOGS
-- ============================================
CREATE TABLE university_queue_logs (
    id INT PRIMARY KEY AUTO_INCREMENT,
    token_number VARCHAR(20),
    officer_id INT,
    action VARCHAR(50),
    action_details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- 5. DAILY STATISTICS
-- ============================================
CREATE TABLE daily_stats (
    id INT PRIMARY KEY AUTO_INCREMENT,
    stat_date DATE NOT NULL,
    office_id INT,
    total_tokens INT DEFAULT 0,
    total_completed INT DEFAULT 0,
    total_skipped INT DEFAULT 0,
    avg_wait_time DECIMAL(10,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE office_messages (
    id INT PRIMARY KEY AUTO_INCREMENT,
    office_code VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    message_type VARCHAR(20) DEFAULT 'info',
    officer_id INT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (officer_id) REFERENCES officers(id) ON DELETE SET NULL
);
-- ============================================
-- INSERT DEFAULT DATA
-- ============================================

-- Insert service offices
INSERT INTO service_offices (office_code, office_name, description, display_order) VALUES
('REG', 'Registry', 'Student registration and enrollment', 1),
('TRN', 'Transcripts', 'Academic transcript requests', 2),
('TST', 'Testimonials', 'Testimonial letters and recommendations', 3),
('GEN', 'General Inquiry', 'General university inquiries', 4);

-- Insert sample officers (PIN: 1234)
INSERT INTO officers (officer_number, officer_name, email, office_id, pin_code) VALUES
(101, 'Dr. Sarah Mukasa', 'sarah@mak.ac.ug', 1, '1234'),
(102, 'Mr. James Okello', 'james@mak.ac.ug', 2, '1234'),
(103, 'Ms. Grace Nambi', 'grace@mak.ac.ug', 3, '1234'),
(104, 'Mr. Robert Kato', 'robert@mak.ac.ug', 4, '1234');
-- Add admin as a special officer
INSERT INTO officers (officer_number, officer_name, email, pin_code, status, office_id) 
VALUES (999, 'System Administrator', 'admin@mak.ac.ug', 'admin123', 'available', 1);

-- Add column to identify admin users
ALTER TABLE officers ADD COLUMN is_admin BOOLEAN DEFAULT FALSE;

-- Mark officer 999 as admin
UPDATE officers SET is_admin = TRUE WHERE officer_number = 999;

-- Verify
SELECT id, officer_number, officer_name, is_admin FROM officers;