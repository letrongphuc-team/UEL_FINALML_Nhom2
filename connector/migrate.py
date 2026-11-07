"""Additive migration. Never invoke schema.sql against an existing database."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from connector.connector import Connector

ADDITIONS = {
    'User': {'addresses_json': 'JSON NULL'},
    'Order': {
        'checkout_key': 'VARCHAR(100) NULL', 'shipping_address': 'TEXT NULL',
        'customer_name': 'VARCHAR(255) NULL', 'phone': 'VARCHAR(30) NULL',
        'completed_at': 'DATETIME NULL', 'expires_at': 'DATETIME NULL',
        'cancellation_reason': 'TEXT NULL', 'payment_reference': 'VARCHAR(100) NULL',
    },
    'Inventory_Forecast': {
        'run_id': 'VARCHAR(100) NULL', 'period_end': 'DATE NULL',
        'target_role': "VARCHAR(30) DEFAULT 'champion'", 'frequency': "VARCHAR(20) DEFAULT 'monthly'",
        'data_cutoff': 'DATE NULL',
    },
}


def migrate():
    db = Connector()
    db.connect()
    try:
        for table, columns in ADDITIONS.items():
            existing = set(db.queryDataset(f'SHOW COLUMNS FROM `{table}`')['Field'])
            for name, definition in columns.items():
                if name not in existing:
                    db.execute(f'ALTER TABLE `{table}` ADD COLUMN `{name}` {definition}')
        indexes = set(db.queryDataset('SHOW INDEX FROM `Order`')['Key_name'])
        if 'uq_checkout_user' not in indexes:
            db.execute('CREATE UNIQUE INDEX uq_checkout_user ON `Order` (user_id, checkout_key)')
        db.execute('''CREATE TABLE IF NOT EXISTS Password_Reset (
            email VARCHAR(255) PRIMARY KEY, code_hash VARCHAR(255) NOT NULL,
            expires_at DATETIME NOT NULL, attempts INT NOT NULL DEFAULT 0) ENGINE=InnoDB''')
        db.execute('''CREATE TABLE IF NOT EXISTS Forecast_Run (
            run_id VARCHAR(100) PRIMARY KEY, metadata_json JSON NOT NULL,
            predictions_json JSON NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB''')
        db.execute("UPDATE `Order` SET status='shipping' WHERE status='shipped'")
        db.execute("UPDATE `Order` SET status='processing' WHERE status='paid'")
        db.execute("""UPDATE `Order` o SET status=CASE WHEN EXISTS (
            SELECT 1 FROM Payment p WHERE p.order_id=o.order_id AND p.provider LIKE '%Bank%'
            AND p.status='pending') THEN 'payment_pending' ELSE 'processing' END WHERE status='pending'""")
        db.execute("UPDATE `Order` SET expires_at=DATE_ADD(created_at,INTERVAL 30 MINUTE) WHERE status='payment_pending' AND expires_at IS NULL")
    finally:
        db.close()


if __name__ == '__main__':
    migrate()
    print('Additive migrations complete; existing data preserved.')
