USE haircare_shop;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS Inventory_Recommendation;
DROP TABLE IF EXISTS Inventory_Forecast;
DROP TABLE IF EXISTS Daily_Sales;
DROP TABLE IF EXISTS Product_Source_Mapping;
DROP TABLE IF EXISTS Inventory_Adjustment;
DROP TABLE IF EXISTS Settings;
DROP TABLE IF EXISTS Quiz_Event;
DROP TABLE IF EXISTS Hair_Profile;
DROP TABLE IF EXISTS Review;
DROP TABLE IF EXISTS Voucher_Redemption;
DROP TABLE IF EXISTS Voucher;
DROP TABLE IF EXISTS Return_Item;
DROP TABLE IF EXISTS Return_Request;
DROP TABLE IF EXISTS Payment;
DROP TABLE IF EXISTS Order_Item;
DROP TABLE IF EXISTS `Order`;
DROP TABLE IF EXISTS Cart_Item;
DROP TABLE IF EXISTS Cart;
DROP TABLE IF EXISTS Product_Variant;
DROP TABLE IF EXISTS Product;
DROP TABLE IF EXISTS Admin_User;
DROP TABLE IF EXISTS `User`;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE `User` (
  user_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(255) NOT NULL UNIQUE,
  password_hash VARCHAR(255), password_salt VARCHAR(255),
  full_name VARCHAR(255), phone VARCHAR(30), status VARCHAR(20) DEFAULT 'active',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE Admin_User (
  admin_user_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(255) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL, password_salt VARCHAR(255),
  full_name VARCHAR(255), role VARCHAR(30) DEFAULT 'admin',
  status VARCHAR(20) DEFAULT 'active', created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE Product (
  product_id VARCHAR(50) PRIMARY KEY,
  name VARCHAR(255) NOT NULL, brand VARCHAR(100), category VARCHAR(100), categories TEXT,
  description TEXT, key_benefits TEXT, ingredients TEXT, how_to_use TEXT,
  product_url VARCHAR(2048), rating DECIMAL(3,2) DEFAULT 0,
  review_count INT UNSIGNED DEFAULT 0, image_url VARCHAR(2048), image_local_path VARCHAR(1024),
  availability BOOLEAN DEFAULT TRUE, source_availability BOOLEAN DEFAULT TRUE,
  is_simulated BOOLEAN DEFAULT TRUE, category_vi VARCHAR(100), catalog_id VARCHAR(100),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE Product_Variant (
  variant_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  product_id VARCHAR(50) NOT NULL, sku_code VARCHAR(100) UNIQUE,
  variant_name VARCHAR(255), size VARCHAR(100),
  price DECIMAL(12,2) NOT NULL DEFAULT 0, currency CHAR(3) NOT NULL DEFAULT 'USD',
  on_hand INT UNSIGNED NOT NULL DEFAULT 0, reserved INT UNSIGNED NOT NULL DEFAULT 0,
  incoming INT UNSIGNED NOT NULL DEFAULT 0, is_active BOOLEAN DEFAULT TRUE,
  CONSTRAINT fk_variant_product FOREIGN KEY (product_id) REFERENCES Product(product_id)
) ENGINE=InnoDB;

CREATE TABLE Cart (
  cart_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT UNSIGNED NULL, session_id VARCHAR(100), status VARCHAR(20) DEFAULT 'active',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_cart_user FOREIGN KEY (user_id) REFERENCES `User`(user_id)
) ENGINE=InnoDB;

CREATE TABLE Cart_Item (
  cart_item_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  cart_id BIGINT UNSIGNED NOT NULL, variant_id BIGINT UNSIGNED NOT NULL,
  quantity INT UNSIGNED NOT NULL, added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_cartitem_cart FOREIGN KEY (cart_id) REFERENCES Cart(cart_id),
  CONSTRAINT fk_cartitem_variant FOREIGN KEY (variant_id) REFERENCES Product_Variant(variant_id),
  UNIQUE KEY uq_cart_variant (cart_id, variant_id)
) ENGINE=InnoDB;

CREATE TABLE `Order` (
  order_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT UNSIGNED NULL, status VARCHAR(30) NOT NULL DEFAULT 'pending',
  subtotal DECIMAL(12,2) NOT NULL DEFAULT 0, discount_total DECIMAL(12,2) NOT NULL DEFAULT 0,
  shipping_fee DECIMAL(12,2) NOT NULL DEFAULT 0, order_total DECIMAL(12,2) NOT NULL DEFAULT 0,
  currency CHAR(3) NOT NULL DEFAULT 'USD', created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  paid_at DATETIME NULL, cancelled_at DATETIME NULL,
  CONSTRAINT fk_order_user FOREIGN KEY (user_id) REFERENCES `User`(user_id)
) ENGINE=InnoDB;

CREATE TABLE Order_Item (
  order_item_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  order_id BIGINT UNSIGNED NOT NULL, variant_id BIGINT UNSIGNED NOT NULL,
  quantity INT UNSIGNED NOT NULL, unit_price_snapshot DECIMAL(12,2) NOT NULL,
  line_total_snapshot DECIMAL(12,2) NOT NULL, product_name_snapshot VARCHAR(255) NOT NULL,
  variant_name_snapshot VARCHAR(255), created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_orderitem_order FOREIGN KEY (order_id) REFERENCES `Order`(order_id) ON DELETE CASCADE,
  CONSTRAINT fk_orderitem_variant FOREIGN KEY (variant_id) REFERENCES Product_Variant(variant_id)
) ENGINE=InnoDB;

CREATE TABLE Payment (
  payment_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  order_id BIGINT UNSIGNED NOT NULL, provider VARCHAR(50), provider_transaction_id VARCHAR(255),
  amount DECIMAL(12,2) NOT NULL, currency CHAR(3) NOT NULL DEFAULT 'USD',
  status VARCHAR(30) NOT NULL DEFAULT 'pending', paid_at DATETIME NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_payment_order FOREIGN KEY (order_id) REFERENCES `Order`(order_id)
) ENGINE=InnoDB;

CREATE TABLE Return_Request (
  return_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  order_id BIGINT UNSIGNED NOT NULL, user_id BIGINT UNSIGNED NULL,
  reason TEXT, status VARCHAR(30) NOT NULL DEFAULT 'requested',
  requested_at DATETIME DEFAULT CURRENT_TIMESTAMP, resolved_at DATETIME NULL,
  CONSTRAINT fk_return_order FOREIGN KEY (order_id) REFERENCES `Order`(order_id),
  CONSTRAINT fk_return_user FOREIGN KEY (user_id) REFERENCES `User`(user_id)
) ENGINE=InnoDB;

CREATE TABLE Return_Item (
  return_item_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  return_id BIGINT UNSIGNED NOT NULL, order_item_id BIGINT UNSIGNED NOT NULL,
  quantity INT UNSIGNED NOT NULL, refund_amount DECIMAL(12,2) DEFAULT 0,
  CONSTRAINT fk_returnitem_return FOREIGN KEY (return_id) REFERENCES Return_Request(return_id),
  CONSTRAINT fk_returnitem_orderitem FOREIGN KEY (order_item_id) REFERENCES Order_Item(order_item_id)
) ENGINE=InnoDB;

CREATE TABLE Voucher (
  voucher_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(100) NOT NULL UNIQUE, discount_type VARCHAR(20) NOT NULL,
  discount_value DECIMAL(12,2) NOT NULL, minimum_order_amount DECIMAL(12,2) DEFAULT 0,
  usage_limit INT UNSIGNED NULL, start_at DATETIME NULL, end_at DATETIME NULL,
  status VARCHAR(20) DEFAULT 'active', created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE Voucher_Redemption (
  redemption_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  voucher_id BIGINT UNSIGNED NOT NULL, user_id BIGINT UNSIGNED NULL,
  order_id BIGINT UNSIGNED NOT NULL, discount_amount DECIMAL(12,2) NOT NULL,
  redeemed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_redemption_voucher FOREIGN KEY (voucher_id) REFERENCES Voucher(voucher_id),
  CONSTRAINT fk_redemption_user FOREIGN KEY (user_id) REFERENCES `User`(user_id),
  CONSTRAINT fk_redemption_order FOREIGN KEY (order_id) REFERENCES `Order`(order_id),
  UNIQUE KEY uq_order_voucher (order_id, voucher_id)
) ENGINE=InnoDB;

CREATE TABLE Review (
  review_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  product_id VARCHAR(50) NOT NULL, user_id BIGINT UNSIGNED NOT NULL,
  order_item_id BIGINT UNSIGNED NULL, rating TINYINT UNSIGNED NOT NULL,
  title VARCHAR(255), content TEXT, status VARCHAR(20) DEFAULT 'published',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_review_product FOREIGN KEY (product_id) REFERENCES Product(product_id),
  CONSTRAINT fk_review_user FOREIGN KEY (user_id) REFERENCES `User`(user_id),
  CONSTRAINT fk_review_orderitem FOREIGN KEY (order_item_id) REFERENCES Order_Item(order_item_id),
  CHECK (rating BETWEEN 1 AND 5)
) ENGINE=InnoDB;

CREATE TABLE Hair_Profile (
  hair_profile_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT UNSIGNED NOT NULL, hair_type VARCHAR(100), hair_condition VARCHAR(100),
  scalp_condition VARCHAR(100), goals_json JSON NULL, profile_version INT DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_hairprofile_user FOREIGN KEY (user_id) REFERENCES `User`(user_id)
) ENGINE=InnoDB;

CREATE TABLE Quiz_Event (
  quiz_event_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT UNSIGNED NULL, session_id VARCHAR(100) NOT NULL,
  answers_json JSON NOT NULL, recommended_product_ids_json JSON NULL,
  started_at DATETIME DEFAULT CURRENT_TIMESTAMP, completed_at DATETIME NULL,
  CONSTRAINT fk_quiz_user FOREIGN KEY (user_id) REFERENCES `User`(user_id)
) ENGINE=InnoDB;

CREATE TABLE Settings (
  setting_key VARCHAR(100) PRIMARY KEY, setting_value TEXT NOT NULL,
  value_type VARCHAR(20) DEFAULT 'string', updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE Inventory_Adjustment (
  adjustment_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  variant_id BIGINT UNSIGNED NOT NULL, admin_user_id BIGINT UNSIGNED NULL,
  adjustment_type VARCHAR(30) NOT NULL, quantity_delta INT NOT NULL,
  reason VARCHAR(255), reference_type VARCHAR(30), reference_id BIGINT UNSIGNED NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_adjustment_variant FOREIGN KEY (variant_id) REFERENCES Product_Variant(variant_id),
  CONSTRAINT fk_adjustment_admin FOREIGN KEY (admin_user_id) REFERENCES Admin_User(admin_user_id)
) ENGINE=InnoDB;

CREATE TABLE Daily_Sales (
  sales_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  product_id VARCHAR(50) NOT NULL, variant_id BIGINT UNSIGNED NULL,
  sale_date DATE NOT NULL, quantity_sold INT NOT NULL, revenue DECIMAL(14,2) NOT NULL,
  currency CHAR(3) NOT NULL DEFAULT 'GBP', source_order_count INT UNSIGNED DEFAULT 0,
  unit_price DECIMAL(12,4) NULL, outlier_source_lines INT UNSIGNED DEFAULT 0,
  is_simulated BOOLEAN DEFAULT TRUE, source_dataset VARCHAR(255),
  CONSTRAINT fk_dailysales_product FOREIGN KEY (product_id) REFERENCES Product(product_id),
  CONSTRAINT fk_dailysales_variant FOREIGN KEY (variant_id) REFERENCES Product_Variant(variant_id),
  UNIQUE KEY uq_daily_product_date (product_id, sale_date)
) ENGINE=InnoDB;

CREATE TABLE Product_Source_Mapping (
  product_id VARCHAR(50) PRIMARY KEY, source_stock_code VARCHAR(100),
  source_description VARCHAR(255), source_median_price_gbp DECIMAL(12,4),
  source_total_quantity BIGINT UNSIGNED, source_sku_rank INT,
  source_dataset VARCHAR(255), is_simulated BOOLEAN DEFAULT TRUE,
  mapping_type VARCHAR(255), mapping_rule TEXT,
  CONSTRAINT fk_mapping_product FOREIGN KEY (product_id) REFERENCES Product(product_id)
) ENGINE=InnoDB;

CREATE TABLE Inventory_Forecast (
  forecast_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  product_id VARCHAR(50) NOT NULL, variant_id BIGINT UNSIGNED NULL,
  forecast_date DATE NULL, horizon_days INT UNSIGNED NOT NULL DEFAULT 7,
  forecast_quantity DECIMAL(14,3), forecast_revenue DECIMAL(14,2), revenue_currency CHAR(3),
  model_name VARCHAR(100), created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_forecast_product FOREIGN KEY (product_id) REFERENCES Product(product_id),
  CONSTRAINT fk_forecast_variant FOREIGN KEY (variant_id) REFERENCES Product_Variant(variant_id)
) ENGINE=InnoDB;

CREATE TABLE Inventory_Recommendation (
  recommendation_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  forecast_id BIGINT UNSIGNED NULL, product_id VARCHAR(50) NOT NULL,
  variant_id BIGINT UNSIGNED NULL, current_stock INT UNSIGNED DEFAULT 0,
  safety_stock DECIMAL(14,3) DEFAULT 0, recommended_order_quantity DECIMAL(14,3) DEFAULT 0,
  lead_time_days INT UNSIGNED DEFAULT 7, review_period_days INT UNSIGNED DEFAULT 7,
  recent_quantity_std DECIMAL(14,3), created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_recommendation_forecast FOREIGN KEY (forecast_id) REFERENCES Inventory_Forecast(forecast_id),
  CONSTRAINT fk_recommendation_product FOREIGN KEY (product_id) REFERENCES Product(product_id),
  CONSTRAINT fk_recommendation_variant FOREIGN KEY (variant_id) REFERENCES Product_Variant(variant_id)
) ENGINE=InnoDB;

CREATE INDEX idx_order_user ON `Order`(user_id);
CREATE INDEX idx_order_created ON `Order`(created_at);
CREATE INDEX idx_orderitem_order ON Order_Item(order_id);
CREATE INDEX idx_orderitem_variant ON Order_Item(variant_id);
CREATE INDEX idx_adjustment_variant ON Inventory_Adjustment(variant_id);
CREATE INDEX idx_daily_product_date ON Daily_Sales(product_id, sale_date);
CREATE INDEX idx_product_category ON Product(category);
