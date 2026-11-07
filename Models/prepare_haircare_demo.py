"""Rebuild the cleaned HairCare demo datasets and audit/EDA reports."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd


CATEGORY_ORDER = [
    "shampoo",
    "hairmask",
    "treatment",
    "dry shampoo",
    "conditioner",
    "hairstyling",
]
CATEGORY_VI = {
    "shampoo": "Dầu gội",
    "hairmask": "Ủ tóc",
    "treatment": "Dưỡng tóc",
    "dry shampoo": "Dầu gội khô",
    "conditioner": "Dầu xả",
    "hairstyling": "Tạo kiểu tóc",
}
OUTLIER_QUANTITY_THRESHOLD = 100
NON_PRODUCT_PATTERN = (
    r"POSTAGE|CARRIAGE|BANK CHARGES|DOTCOM|AMAZON FEE|ADJUST|"
    r"MANUAL|SAMPLES|CRUK"
)
RETAIL_COLUMNS = [
    "Invoice",
    "StockCode",
    "Description",
    "Quantity",
    "InvoiceDate",
    "Price",
    "Customer ID",
    "Country",
]
RETAIL_LINE_COLUMNS = [
    "Invoice",
    "StockCode",
    "Description",
    "Quantity",
    "InvoiceDate",
    "Price",
    "Customer ID",
    "Country",
]


def find_data_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__).resolve().parent).resolve()
    for candidate in [current, *current.parents]:
        data_root = candidate / "data" if (candidate / "data" / "raw").is_dir() else candidate
        raw_root = data_root / "raw"
        if (
            (raw_root / "products_crawl.csv").is_file()
            and (raw_root / "ecomcosmetic").is_dir()
            and (raw_root / "online+retail+ii (1)").is_dir()
        ):
            return data_root
    raise FileNotFoundError(
        "Không tìm thấy raw/products_crawl.csv, raw/ecomcosmetic/ và "
        "raw/online+retail+ii (1)/ từ thư mục script hoặc thư mục cha."
    )


def clean_text(value: object) -> object:
    if pd.isna(value):
        return pd.NA
    text = unicodedata.normalize("NFKC", str(value)).replace("\ufffd", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text if text else pd.NA


def audit_frame(
    dataset: str,
    stage: str,
    frame: pd.DataFrame,
    note: str,
    key_columns: list[str] | None = None,
    exact_duplicate_rows: int | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    row_count = len(frame)
    column_count = len(frame.columns)
    for column in frame.columns:
        missing_count = int(frame[column].isna().sum())
        rows.append(
            {
                "dataset": dataset,
                "stage": stage,
                "row_count": row_count,
                "column_count": column_count,
                "field_or_check": column,
                "missing_count": missing_count,
                "missing_percent": missing_count / row_count if row_count else 0.0,
                "unique_non_null": int(frame[column].nunique(dropna=True)),
                "duplicate_excess_rows": 0,
                "note": note,
            }
        )
    if exact_duplicate_rows is None:
        exact_duplicate_rows = int(frame.duplicated().sum())
    rows.append(
        {
            "dataset": dataset,
            "stage": stage,
            "row_count": row_count,
            "column_count": column_count,
            "field_or_check": "__exact_duplicate_rows__",
            "missing_count": 0,
            "missing_percent": 0.0,
            "unique_non_null": np.nan,
            "duplicate_excess_rows": exact_duplicate_rows,
            "note": "Số dòng lặp dư; giữ nguyên nếu chưa có khóa dòng để phân biệt giao dịch hợp lệ.",
        }
    )
    if key_columns:
        missing_keys = int(frame[key_columns].isna().any(axis=1).sum())
        complete_keys = frame.dropna(subset=key_columns)
        duplicate_keys = int(complete_keys.duplicated(key_columns).sum())
        rows.append(
            {
                "dataset": dataset,
                "stage": stage,
                "row_count": row_count,
                "column_count": column_count,
                "field_or_check": "__business_key__:" + "+".join(key_columns),
                "missing_count": missing_keys,
                "missing_percent": missing_keys / row_count if row_count else 0.0,
                "unique_non_null": int(
                    frame.dropna(subset=key_columns)[key_columns]
                    .drop_duplicates()
                    .shape[0]
                ),
                "duplicate_excess_rows": duplicate_keys,
                "note": "Missing_count là số dòng thiếu một phần khóa; duplicate_excess_rows chỉ tính trên các dòng đủ khóa.",
            }
        )
    return rows


def normalize_retail(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["Invoice"] = (
        result["Invoice"]
        .astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.upper()
    )
    result["StockCode"] = result["StockCode"].astype("string").str.strip().str.upper()
    result["Description"] = result["Description"].map(clean_text).astype("string")
    result["InvoiceDate"] = pd.to_datetime(result["InvoiceDate"], errors="coerce")
    result["Quantity"] = pd.to_numeric(result["Quantity"], errors="coerce")
    result["Price"] = pd.to_numeric(result["Price"], errors="coerce")
    result["Country"] = result["Country"].map(clean_text).astype("string")
    return result


def invoice_signature(frame: pd.DataFrame) -> pd.Series:
    def signature(group: pd.DataFrame) -> tuple[tuple[str, ...], ...]:
        values = []
        line_columns = [
            column for column in RETAIL_LINE_COLUMNS if column != "Invoice"
        ]
        for record in group[line_columns].itertuples(index=False, name=None):
            values.append(
                tuple("" if pd.isna(value) else str(value) for value in record)
            )
        return tuple(sorted(values))

    return frame.groupby("Invoice", sort=False).apply(
        signature, include_groups=False
    )


def main() -> None:
    data_root = find_data_root()
    raw_root = data_root / "raw"
    output_dir = data_root / "processed_data_demo"
    output_dir.mkdir(parents=True, exist_ok=True)
    quality_rows: list[dict[str, object]] = []

    cosmetic_path = raw_root / "ecomcosmetic" / "E-commerce  cosmetic dataset.csv"
    products_path = raw_root / "products_crawl.csv"
    retail_path = raw_root / "online+retail+ii (1)" / "online_retail_II.xlsx"

    # Reference cosmetic listings: retain source values and flag incomplete records.
    cosmetics = pd.read_csv(cosmetic_path, encoding="cp1252", low_memory=False)
    quality_rows.extend(
        audit_frame(
            cosmetic_path.name,
            "raw",
            cosmetics,
            "Source listing; missing optional descriptive/review fields are retained.",
        )
    )
    required_cosmetic = {
        "product_name",
        "website",
        "country",
        "category",
        "subcategory",
        "price",
        "brand",
    }
    missing_columns = required_cosmetic - set(cosmetics.columns)
    if missing_columns:
        raise ValueError(f"Thiếu cột bắt buộc trong cosmetic CSV: {sorted(missing_columns)}")

    catalog = cosmetics.copy()
    catalog.insert(0, "source_row_id", np.arange(2, len(catalog) + 2))
    text_columns = [
        "product_name",
        "website",
        "country",
        "category",
        "subcategory",
        "brand",
        "ingredients",
        "form",
        "type",
        "size",
    ]
    for column in text_columns:
        if column in catalog.columns:
            catalog[f"{column}_source"] = catalog[column]
            catalog[column] = catalog[column].map(clean_text).astype("string")

    catalog["encoding_warning"] = catalog["product_name_source"].astype(
        "string"
    ).str.contains("\ufffd", regex=False, na=False)
    catalog["price"] = pd.to_numeric(catalog["price"], errors="coerce")
    catalog["rating"] = pd.to_numeric(catalog.get("rating"), errors="coerce")
    catalog["noofratings"] = pd.to_numeric(
        catalog.get(
            "noofratings", pd.Series(index=catalog.index, dtype="string")
        )
        .astype("string")
        .str.replace(",", "", regex=False),
        errors="coerce",
    )
    catalog["size_numeric"] = pd.to_numeric(
        catalog["size"]
        .astype("string")
        .str.extract(r"(\d+(?:\.\d+)?)", expand=False),
        errors="coerce",
    )

    hair = catalog.loc[
        catalog["category"].str.casefold().eq("hair").fillna(False)
    ].copy()
    hair = hair.loc[hair["product_name"].notna() & hair["brand"].notna()].copy()
    hair["name_norm"] = (
        hair["product_name"]
        .str.casefold()
        .str.replace(r"[^\w]+", " ", regex=True)
        .str.strip()
        .replace("", pd.NA)
    )
    hair["brand_norm"] = (
        hair["brand"]
        .str.casefold()
        .str.replace(r"[^\w]+", " ", regex=True)
        .str.strip()
        .replace("", pd.NA)
    )
    hair["size_norm"] = (
        hair["size"]
        .str.casefold()
        .str.replace(r"\s+", "", regex=True)
        .replace("", pd.NA)
    )
    category_labels = {category: category for category in CATEGORY_ORDER}
    category_labels.update({"oil": "treatment", "serum": "treatment"})
    hair["subcategory_normalized"] = (
        hair["subcategory"]
        .str.casefold()
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )
    hair["suggested_haircare_category"] = (
        hair["subcategory_normalized"].map(category_labels).astype("string")
    )
    hair["category_mapping_method"] = np.where(
        hair["subcategory_normalized"].isin(["oil", "serum"]),
        "Oil/serum subcategory merged into treatment",
        np.where(
            hair["suggested_haircare_category"].notna(),
            "Direct mapping from dataset subcategory",
            "Cần rà soát thủ công",
        ),
    )
    hair["needs_manual_review"] = (
        hair["suggested_haircare_category"].isna()
        | hair["price"].isna()
        | hair["price"].le(0)
        | hair["encoding_warning"]
        | hair["name_norm"].isna()
        | hair["brand_norm"].isna()
    )
    hair["source_price_unit"] = "Chưa xác minh; giữ nguyên giá nguồn"
    hair["completeness_score"] = (
        hair["price"].notna().astype(int)
        + hair["ingredients"].notna().astype(int)
        + hair["rating"].notna().astype(int)
        + (~hair["encoding_warning"]).astype(int)
    )
    hair = hair.sort_values(
        ["completeness_score", "noofratings", "source_row_id"],
        ascending=[False, False, True],
        kind="stable",
        na_position="last",
    )
    business_key_columns = ["name_norm", "brand_norm", "size_norm"]
    complete_business_key = hair[business_key_columns].notna().all(axis=1)
    hair["duplicate_listing"] = False
    hair.loc[complete_business_key, "duplicate_listing"] = hair.loc[
        complete_business_key
    ].duplicated(business_key_columns, keep="first")
    hair_clean = hair.loc[~hair["duplicate_listing"]].copy()
    hair_clean["catalog_id"] = hair_clean.apply(
        lambda row: (
            "HC-CAT-"
            + hashlib.sha1(
                f"{row['name_norm']}|{row['brand_norm']}|{row['size_norm']}".encode(
                    "utf-8"
                )
            )
            .hexdigest()[:12]
            .upper()
            if pd.notna(row[business_key_columns]).all()
            else f"HC-CAT-SRC-{int(row['source_row_id'])}"
        ),
        axis=1,
    )
    if hair_clean["catalog_id"].isna().any() or hair_clean["catalog_id"].duplicated().any():
        raise ValueError("catalog_id của cosmetic reference phải tồn tại và duy nhất.")
    quality_rows.extend(
        audit_frame(
            "cosmetic_hair_reference_cleaned.csv",
            "cleaned",
            hair_clean.drop(columns=["completeness_score", "duplicate_listing"]),
            "Hair listings; duplicate key is normalized name + brand + size; source price unit is unverified.",
            key_columns=business_key_columns,
        )
    )
    quality_rows.extend(
        audit_frame(
            "cosmetic_hair_reference_cleaned.csv",
            "cleaned",
            hair_clean,
            "Generated catalog_id must remain unique, including listings with incomplete business keys.",
            key_columns=["catalog_id"],
        )[-1:]
    )

    # Main web catalog; simulated availability is explicit and source availability is preserved.
    products_crawl = pd.read_csv(products_path, encoding="utf-8-sig", low_memory=False)
    quality_rows.extend(
        audit_frame(
            products_path.name,
            "raw",
            products_crawl,
            "Crawled product catalog; source availability is not altered in raw.",
            key_columns=["product_id"],
        )
    )
    crawl_required = {
        "product_id",
        "product_name",
        "brand",
        "category",
        "price",
        "currency",
        "availability",
    }
    missing_columns = crawl_required - set(products_crawl.columns)
    if missing_columns:
        raise ValueError(f"Thiếu cột bắt buộc trong products_crawl.csv: {sorted(missing_columns)}")

    crawl_catalog = products_crawl.copy()
    crawl_catalog["product_id"] = (
        crawl_catalog["product_id"]
        .astype("string")
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
    )
    for column in [
        "product_name",
        "brand",
        "category",
        "size",
        "product_description",
        "key_benefits",
        "ingredients",
        "how_to_use",
        "product_url",
        "source_url",
        "image_url",
        "image_local_path",
    ]:
        if column in crawl_catalog:
            crawl_catalog[column] = crawl_catalog[column].map(clean_text).astype("string")
    crawl_catalog["currency"] = (
        crawl_catalog["currency"].astype("string").str.strip().str.upper()
    )
    crawl_catalog["price_usd"] = pd.to_numeric(crawl_catalog["price"], errors="coerce")
    crawl_catalog["source_availability"] = crawl_catalog["availability"].astype(
        "boolean"
    )
    crawl_catalog["availability"] = True
    crawl_catalog["availability_is_simulated"] = True
    crawl_catalog["is_simulated"] = True

    if crawl_catalog["product_id"].isna().any() or crawl_catalog["product_id"].duplicated().any():
        raise ValueError("product_id trong products_crawl.csv phải tồn tại và duy nhất.")
    if set(crawl_catalog["currency"].dropna().unique()) != {"USD"}:
        raise ValueError("Currency crawl không đồng nhất USD.")
    if crawl_catalog["price_usd"].isna().any() or crawl_catalog["price_usd"].le(0).any():
        raise ValueError("Có giá USD thiếu hoặc không hợp lệ trong catalog crawl.")

    crawl_category_map = {
        "Shampoo": "shampoo",
        "Hair Masks": "hairmask",
        "Treatment / Oil": "treatment",
        "Dry Shampoo": "dry shampoo",
        "Conditioner": "conditioner",
        "Styling & Finishing": "hairstyling",
    }
    crawl_catalog["category_source"] = crawl_catalog["category"]
    crawl_catalog["category"] = (
        crawl_catalog["category_source"].map(crawl_category_map).astype("string")
    )
    serum_in_styling = (
        crawl_catalog["category_source"].eq("Styling & Finishing")
        & crawl_catalog["product_name"].str.contains(
            r"\bserum\b", case=False, regex=True, na=False
        )
    )
    crawl_catalog.loc[serum_in_styling, "category"] = "treatment"
    if "categories" in crawl_catalog.columns:
        crawl_catalog["categories"] = crawl_catalog["category"]
    crawl_catalog["category_vi"] = crawl_catalog["category"].map(CATEGORY_VI).astype(
        "string"
    )
    crawl_catalog["category_mapping_rule"] = np.select(
        [
            serum_in_styling,
            crawl_catalog["category_source"].eq("Treatment / Oil"),
        ],
        [
            "Styling & Finishing + product name contains serum -> treatment",
            "Treatment / Oil -> treatment",
        ],
        default="Direct source category mapping",
    )
    if (
        crawl_catalog["category"].isna().any()
        or set(crawl_catalog["category"].unique()) != set(CATEGORY_ORDER)
    ):
        raise ValueError("Taxonomy crawl chưa ánh xạ đủ đúng 6 danh mục.")
    crawl_catalog["price_unit"] = "USD"
    crawl_catalog["catalog_source"] = "raw/products_crawl.csv"
    crawl_catalog["needs_manual_review"] = False
    crawl_catalog["catalog_id"] = "HC-" + crawl_catalog["product_id"]
    category_rank = {name: index for index, name in enumerate(CATEGORY_ORDER)}
    crawl_catalog["_category_order"] = crawl_catalog["category"].map(category_rank)
    crawl_catalog = crawl_catalog.sort_values(
        ["_category_order", "product_name", "product_id"], kind="stable"
    ).drop(columns="_category_order").reset_index(drop=True)
    quality_rows.extend(
        audit_frame(
            "haircare_catalog_cleaned.csv",
            "cleaned",
            crawl_catalog,
            "Catalog demo; availability is simulated true for all items; source_availability preserves crawl value.",
            key_columns=["product_id"],
        )
    )

    # Read and clean Online Retail II. Cross-sheet duplicate invoices are removed
    # only after verifying that all normalized line contents match.
    excel_file = pd.ExcelFile(retail_path, engine="openpyxl")
    seen_invoice_ids: set[str] = set()
    normalized_by_sheet: dict[str, pd.DataFrame] = {}
    for sheet_name in excel_file.sheet_names:
        frame = pd.read_excel(
            excel_file, sheet_name=sheet_name, usecols=RETAIL_COLUMNS, engine="openpyxl"
        )
        normalized = normalize_retail(frame)
        normalized_by_sheet[sheet_name] = normalized
        quality_rows.extend(
            audit_frame(
                f"{retail_path.name}:{sheet_name}",
                "raw",
                normalized,
                "Raw workbook sheet after type/whitespace normalization; source values are not rewritten.",
                key_columns=["Invoice", "StockCode", "InvoiceDate"],
            )
        )

    if len(normalized_by_sheet) >= 2:
        first_name, *later_names = excel_file.sheet_names
        first = normalized_by_sheet[first_name]
        first_signature = invoice_signature(first.dropna(subset=["Invoice"]))
        for sheet_name in later_names:
            frame = normalized_by_sheet[sheet_name]
            overlap = set(first_signature.index).intersection(
                frame["Invoice"].dropna().unique()
            )
            if overlap:
                later_signature = invoice_signature(
                    frame.loc[frame["Invoice"].isin(overlap)]
                )
                shared = set(first_signature.index).intersection(later_signature.index)
                conflicting = [
                    invoice
                    for invoice in shared
                    if first_signature.loc[invoice] != later_signature.loc[invoice]
                ]
                if conflicting:
                    raise ValueError(
                        f"Invoice trùng nhưng nội dung khác giữa sheet {first_name} "
                        f"và {sheet_name}; ví dụ: {conflicting[:5]}"
                    )

    transaction_frames: list[pd.DataFrame] = []
    retail_quality_rows: list[dict[str, object]] = []
    for sheet_name in excel_file.sheet_names:
        frame = normalized_by_sheet[sheet_name]
        source_row_count = len(frame)
        duplicate_invoice_mask = frame["Invoice"].isin(seen_invoice_ids)
        duplicate_invoice_ids = int(frame.loc[duplicate_invoice_mask, "Invoice"].nunique())
        duplicate_invoice_rows = int(duplicate_invoice_mask.sum())
        frame = frame.loc[~duplicate_invoice_mask].copy()
        seen_invoice_ids.update(frame["Invoice"].dropna().unique().tolist())

        reason = pd.Series(pd.NA, index=frame.index, dtype="string")
        cancelled = frame["Invoice"].str.startswith("C", na=False)
        reason.loc[cancelled] = "cancelled_invoice"
        invalid_quantity = frame["Quantity"].isna() | frame["Quantity"].le(0)
        mask = reason.isna() & invalid_quantity
        reason.loc[mask] = "quantity_missing_or_nonpositive"
        invalid_price = frame["Price"].isna() | frame["Price"].le(0)
        mask = reason.isna() & invalid_price
        reason.loc[mask] = "price_missing_or_nonpositive"
        invalid_date = frame["InvoiceDate"].isna()
        mask = reason.isna() & invalid_date
        reason.loc[mask] = "date_missing_or_invalid"
        invalid_sku = frame["StockCode"].isna() | frame["StockCode"].eq("")
        mask = reason.isna() & invalid_sku
        reason.loc[mask] = "sku_missing"
        valid_sale = reason.isna()
        sales = frame.loc[valid_sale].copy()
        sales["revenue_source"] = sales["Quantity"] * sales["Price"]
        sales["sale_date"] = sales["InvoiceDate"].dt.floor("D")
        sales["quantity_outlier_gt_100"] = sales["Quantity"].gt(
            OUTLIER_QUANTITY_THRESHOLD
        )
        sales["currency"] = "GBP"
        sales["source_dataset"] = "Online Retail II"
        sales["is_simulated"] = False

        invalid_reason_counts = reason.value_counts(dropna=True)
        raw_exact_duplicates = int(
            normalized_by_sheet[sheet_name].duplicated(subset=RETAIL_LINE_COLUMNS).sum()
        )
        retained_exact_duplicates = int(
            sales.duplicated(subset=RETAIL_LINE_COLUMNS).sum()
        )
        retail_quality_rows.append(
            {
                "sheet": sheet_name,
                "source_rows": source_row_count,
                "raw_exact_duplicate_rows_excess": raw_exact_duplicates,
                "duplicate_invoice_ids_removed": duplicate_invoice_ids,
                "duplicate_invoice_rows_removed": duplicate_invoice_rows,
                "cancelled_rows_excluded": int(invalid_reason_counts.get("cancelled_invoice", 0)),
                "quantity_nonpositive_or_missing_excluded": int(
                    invalid_reason_counts.get("quantity_missing_or_nonpositive", 0)
                ),
                "price_nonpositive_or_missing_excluded": int(
                    invalid_reason_counts.get("price_missing_or_nonpositive", 0)
                ),
                "date_missing_excluded": int(
                    invalid_reason_counts.get("date_missing_or_invalid", 0)
                ),
                "sku_missing_excluded": int(invalid_reason_counts.get("sku_missing", 0)),
                "valid_sales_rows": len(sales),
                "missing_description_in_valid_sales": int(sales["Description"].isna().sum()),
                "missing_customer_id_in_source": int(
                    normalized_by_sheet[sheet_name]["Customer ID"].isna().sum()
                ),
                "retained_exact_duplicate_rows_excess": retained_exact_duplicates,
                "exclusion_reason_note": (
                    "Các số loại trừ theo lý do là mutually exclusive; ưu tiên "
                    "invoice hủy > quantity > price > date > SKU. Exact duplicate "
                    "trong cùng sheet được giữ và báo cáo vì không có transaction-line ID."
                ),
            }
        )
        transaction_frames.append(
            sales[
                [
                    "Invoice",
                    "StockCode",
                    "Description",
                    "Country",
                    "InvoiceDate",
                    "sale_date",
                    "Quantity",
                    "Price",
                    "revenue_source",
                    "quantity_outlier_gt_100",
                    "currency",
                    "source_dataset",
                    "is_simulated",
                ]
            ]
        )

    retail_quality = pd.DataFrame(retail_quality_rows)
    transactions_clean = pd.concat(transaction_frames, ignore_index=True)
    transactions_clean["revenue_source"] = transactions_clean["revenue_source"].round(2)
    if (
        int(retail_quality["source_rows"].sum())
        != int(retail_quality["duplicate_invoice_rows_removed"].sum())
        + int(retail_quality["valid_sales_rows"].sum())
        + int(
            retail_quality[
                [
                    "cancelled_rows_excluded",
                    "quantity_nonpositive_or_missing_excluded",
                    "price_nonpositive_or_missing_excluded",
                    "date_missing_excluded",
                    "sku_missing_excluded",
                ]
            ].to_numpy().sum()
        )
    ):
        raise AssertionError("Retail row accounting does not reconcile.")
    quality_rows.extend(
        audit_frame(
            "retail_transactions_cleaned.csv.gz",
            "cleaned",
            transactions_clean,
            "Valid positive-quantity/positive-price source retail rows; not HairCare-labelled sales.",
            key_columns=["Invoice", "StockCode", "InvoiceDate"],
        )
    )

    # Rank eligible source SKUs and assign stable simulated labels to all 59 products.
    description_upper = transactions_clean["Description"].str.upper()
    transactions_clean["description_is_service_or_adjustment"] = description_upper.str.contains(
        NON_PRODUCT_PATTERN, regex=True, na=False
    )
    product_candidates = transactions_clean.loc[
        transactions_clean["Description"].notna()
        & ~transactions_clean["description_is_service_or_adjustment"]
    ].copy()
    sku_activity = (
        product_candidates.groupby("StockCode", as_index=False)
        .agg(
            source_description=("Description", "first"),
            distinct_invoices=("Invoice", "nunique"),
            source_rows=("Invoice", "size"),
            source_median_price=("Price", "median"),
            source_total_quantity=("Quantity", "sum"),
        )
        .sort_values(
            ["distinct_invoices", "source_rows", "StockCode"],
            ascending=[False, False, True],
            kind="stable",
        )
        .reset_index(drop=True)
    )
    demo_products = crawl_catalog.copy()
    source_category_order = {
        "Shampoo": 0,
        "Hair Masks": 1,
        "Treatment / Oil": 2,
        "Dry Shampoo": 4,
        "Conditioner": 5,
        "Styling & Finishing": 6,
    }
    demo_products["_category_order"] = demo_products["category_source"].map(
        source_category_order
    )
    mapping_serum_mask = (
        demo_products["category_source"].eq("Styling & Finishing")
        & demo_products["product_name"].str.contains(
            r"\bserum\b", case=False, regex=True, na=False
        )
    )
    demo_products.loc[mapping_serum_mask, "_category_order"] = 3
    demo_products = demo_products.sort_values(
        ["_category_order", "product_name", "product_id"], kind="stable"
    ).drop(columns="_category_order").reset_index(drop=True)
    sku_activity = sku_activity.head(len(demo_products)).reset_index(drop=True)
    if len(sku_activity) != len(demo_products):
        raise ValueError(
            f"Không đủ SKU nguồn: cần {len(demo_products)}, có {len(sku_activity)}."
        )

    mapping = pd.concat(
        [
            demo_products[
                [
                    "product_id",
                    "product_name",
                    "category",
                    "category_vi",
                    "source_availability",
                ]
            ].reset_index(drop=True),
            sku_activity.reset_index(drop=True),
        ],
        axis=1,
    )
    mapping["source_sku_rank"] = np.arange(1, len(mapping) + 1)
    mapping["source_median_price_gbp"] = mapping["source_median_price"].round(2)
    mapping["source_currency"] = "GBP"
    mapping["source_dataset"] = "Online Retail II"
    mapping["is_simulated"] = True
    mapping["label_is_simulated"] = True
    mapping["mapping_type"] = (
        "SIMULATED LABEL ONLY - source quantities/prices unchanged; not actual HairCare sales"
    )
    mapping["mapping_rule"] = (
        "All 59 crawl products paired one-to-one with top source SKUs by distinct "
        "invoice count, source rows, then StockCode; products sorted by category/name/ID."
    )
    mapping["price_used_for_ml"] = (
        "Original transaction Price in GBP; products_crawl USD price excluded"
    )
    if mapping["product_id"].duplicated().any() or mapping["StockCode"].duplicated().any():
        raise ValueError("Mapping phải là quan hệ một-một giữa product_id và StockCode.")
    quality_rows.extend(
        audit_frame(
            "haircare_demo_product_mapping.csv",
            "simulated_mapping",
            mapping,
            "Simulated product labels; StockCode remains from Online Retail II.",
            key_columns=["product_id"],
        )
    )
    quality_rows.extend(
        audit_frame(
            "haircare_demo_product_mapping.csv",
            "simulated_mapping",
            mapping,
            "Mapping uniqueness check by source SKU.",
            key_columns=["StockCode"],
        )[-1:]
    )

    mapped_transactions = product_candidates.merge(
        mapping[["StockCode", "product_id", "category", "category_vi", "product_name"]],
        on="StockCode",
        how="inner",
        validate="many_to_one",
    )
    mapped_transactions = mapped_transactions.rename(
        columns={
            "Quantity": "quantity_sold",
            "Price": "unit_price_gbp",
            "revenue_source": "revenue_gbp",
        }
    )
    mapped_transactions["quantity_outlier_gt_100"] = mapped_transactions[
        "quantity_sold"
    ].gt(OUTLIER_QUANTITY_THRESHOLD)
    mapped_transactions["currency"] = "GBP"
    mapped_transactions["source_dataset"] = "Online Retail II"
    mapped_transactions["is_simulated"] = True
    mapped_transactions["label_is_simulated"] = True

    daily_observed = mapped_transactions.groupby(
        ["sale_date", "product_id"], as_index=False
    ).agg(
        quantity_sold=("quantity_sold", "sum"),
        revenue_gbp=("revenue_gbp", "sum"),
        source_order_count=("Invoice", "nunique"),
        unit_price_gbp=("unit_price_gbp", "mean"),
        outlier_source_lines=("quantity_outlier_gt_100", "sum"),
    )
    all_dates = pd.date_range(
        transactions_clean["sale_date"].min(),
        transactions_clean["sale_date"].max(),
        freq="D",
        name="sale_date",
    )
    complete_index = pd.MultiIndex.from_product(
        [all_dates, mapping["product_id"].tolist()],
        names=["sale_date", "product_id"],
    )
    daily_demo = (
        daily_observed.set_index(["sale_date", "product_id"])
        .reindex(complete_index)
        .reset_index()
        .merge(
            mapping[
                [
                    "product_id",
                    "category",
                    "category_vi",
                    "product_name",
                    "source_availability",
                ]
            ],
            on="product_id",
            how="left",
            validate="many_to_one",
        )
    )
    for column in [
        "quantity_sold",
        "revenue_gbp",
        "source_order_count",
        "outlier_source_lines",
    ]:
        daily_demo[column] = daily_demo[column].fillna(0)
    daily_demo["currency"] = "GBP"
    daily_demo["source_dataset"] = "Online Retail II"
    daily_demo["is_simulated"] = True
    daily_demo["label_is_simulated"] = True
    daily_demo["is_zero_sale_day"] = daily_demo["quantity_sold"].eq(0)
    daily_demo["quantity_sold"] = daily_demo["quantity_sold"].astype("int64")
    daily_demo["source_order_count"] = daily_demo["source_order_count"].astype("int64")
    daily_demo["outlier_source_lines"] = daily_demo["outlier_source_lines"].astype(
        "int64"
    )
    daily_demo["revenue_gbp"] = daily_demo["revenue_gbp"].round(2)

    # Reconcile the panel against mapped source transaction rows.
    panel_totals = daily_demo[["quantity_sold", "revenue_gbp"]].sum()
    source_totals = mapped_transactions[["quantity_sold", "revenue_gbp"]].sum()
    if int(panel_totals["quantity_sold"]) != int(source_totals["quantity_sold"]):
        raise AssertionError("Daily quantity total differs from mapped source transactions.")
    if not np.isclose(panel_totals["revenue_gbp"], source_totals["revenue_gbp"], atol=0.01):
        raise AssertionError("Daily revenue total differs from mapped source transactions.")

    quality_rows.extend(
        audit_frame(
            "haircare_demo_daily_sales.csv",
            "simulated_daily_panel",
            daily_demo,
            "Zero-sale days are explicit panel rows; unit_price_gbp is intentionally null on those days.",
            key_columns=["sale_date", "product_id"],
        )
    )

    # EDA output tables, with units and simulation markers retained.
    category_eda = daily_demo.groupby(
        ["category", "category_vi"], as_index=False
    ).agg(
        product_count=("product_id", "nunique"),
        panel_rows=("product_id", "size"),
        zero_sale_days=("is_zero_sale_day", "sum"),
        zero_sale_rate=("is_zero_sale_day", "mean"),
        quantity_sold=("quantity_sold", "sum"),
        revenue_gbp=("revenue_gbp", "sum"),
        outlier_source_lines=("outlier_source_lines", "sum"),
    )
    category_eda["revenue_share"] = (
        category_eda["revenue_gbp"] / category_eda["revenue_gbp"].sum()
    )
    category_eda = category_eda.merge(
        crawl_catalog.groupby("category", as_index=False).agg(
            catalog_min_price_usd=("price_usd", "min"),
            catalog_median_price_usd=("price_usd", "median"),
            catalog_max_price_usd=("price_usd", "max"),
        ),
        on="category",
        how="left",
        validate="one_to_one",
    )
    category_eda["currency_revenue"] = "GBP"
    category_eda["currency_catalog_price"] = "USD"
    category_eda["is_simulated_sales"] = True

    product_eda = mapped_transactions.groupby(
        ["product_id", "product_name", "category"], as_index=False
    ).agg(
        mapped_source_transaction_rows=("Invoice", "size"),
        source_invoice_count=("Invoice", "nunique"),
        quantity_sold=("quantity_sold", "sum"),
        revenue_gbp=("revenue_gbp", "sum"),
        outlier_source_lines=("quantity_outlier_gt_100", "sum"),
    )
    product_zero_days = daily_demo.groupby("product_id").agg(
        zero_sale_days=("is_zero_sale_day", "sum"),
        zero_sale_rate=("is_zero_sale_day", "mean"),
        active_days=("is_zero_sale_day", lambda values: int((~values).sum())),
    )
    product_eda = product_eda.merge(
        product_zero_days, on="product_id", validate="one_to_one"
    )
    product_eda["currency_revenue"] = "GBP"
    product_eda["is_simulated_sales"] = True

    daily_demo["month"] = daily_demo["sale_date"].dt.to_period("M").astype("string")
    monthly_eda = daily_demo.groupby("month", as_index=False).agg(
        panel_rows=("product_id", "size"),
        quantity_sold=("quantity_sold", "sum"),
        revenue_gbp=("revenue_gbp", "sum"),
        zero_sale_days=("is_zero_sale_day", "sum"),
    )
    monthly_eda["currency_revenue"] = "GBP"
    monthly_eda["is_simulated_sales"] = True
    daily_demo = daily_demo.drop(columns="month")

    quality_report = pd.DataFrame(
        [
            ("products_crawl.csv", "raw_products", len(products_crawl), "59 crawl rows; USD price source."),
            ("products_crawl.csv", "products_cleaned", len(crawl_catalog), "59 unique products; raw optional-field missingness retained."),
            ("products_crawl.csv", "source_products_unavailable", int((~crawl_catalog["source_availability"].fillna(False)).sum()), "Crawl availability; preserved in source_availability."),
            ("Web demo", "products_available_in_demo", int(crawl_catalog["availability"].sum()), "All product availability is simulated true."),
            ("Web demo", "categories_mapped", crawl_catalog["category"].nunique(), "Six data categories; source Treatment / Oil and Styling & Finishing serum names map to treatment."),
            ("Cosmetic CSV tham khảo", "raw_rows", len(cosmetics), "Reference listing source; price unit unverified."),
            ("Cosmetic CSV tham khảo", "raw_exact_duplicate_rows_excess", int(cosmetics.duplicated().sum()), "Exact duplicate rows in source file."),
            ("Cosmetic CSV tham khảo", "hair_rows_before_business_dedup", len(hair), "Category hair with name and brand."),
            ("Cosmetic CSV tham khảo", "hair_business_duplicates_removed", int(hair["duplicate_listing"].sum()), "Duplicate key: normalized name + brand + size."),
            ("Cosmetic CSV tham khảo", "hair_listings_cleaned", len(hair_clean), "Reference only; not sales."),
            ("Cosmetic CSV tham khảo", "hair_listings_needing_manual_review", int(hair_clean["needs_manual_review"].sum()), "Price/subcategory/encoding/invalid normalized name or brand review flag."),
            ("Online Retail II", "source_rows", int(retail_quality["source_rows"].sum()), "Two raw workbook sheets."),
            ("Online Retail II", "same_invoice_rows_removed", int(retail_quality["duplicate_invoice_rows_removed"].sum()), "Overlapping invoice IDs removed after line-level content match."),
            ("Online Retail II", "valid_sales_rows", len(transactions_clean), "Positive quantity/price and valid invoice/date/SKU."),
            ("Online Retail II", "raw_exact_duplicate_rows_excess", int(retail_quality["raw_exact_duplicate_rows_excess"].sum()), "Within-sheet repeated lines reported; retained because no line ID proves accidental duplication."),
            ("Online Retail II", "mapped_source_transaction_rows", len(mapped_transactions), "Eligible rows for 59 simulated product labels."),
            ("Online Retail II", "demo_products_mapped", len(mapping), "One-to-one product/SKU mapping; labels simulated."),
            ("Online Retail II", "quantity_outlier_lines_kept", int(mapped_transactions["quantity_outlier_gt_100"].sum()), "Quantity >100 retained and flagged."),
            ("Online Retail II", "daily_ml_rows", len(daily_demo), "Complete 59 products x 739 dates."),
            ("Online Retail II", "daily_zero_sale_rows", int(daily_demo["is_zero_sale_day"].sum()), "Zero-sales generated by panel completion, not source row imputation."),
        ],
        columns=["dataset", "metric", "value", "note"],
    )

    # Write all outputs only after data checks pass.
    crawl_catalog.to_csv(
        output_dir / "haircare_catalog_cleaned.csv", index=False, encoding="utf-8-sig"
    )
    hair_clean.drop(
        columns=["completeness_score", "duplicate_listing"], errors="ignore"
    ).to_csv(
        output_dir / "cosmetic_hair_reference_cleaned.csv",
        index=False,
        encoding="utf-8-sig",
    )
    transactions_clean.to_csv(
        output_dir / "retail_transactions_cleaned.csv.gz",
        index=False,
        compression={"method": "gzip", "mtime": 0},
        encoding="utf-8-sig",
    )
    mapping.to_csv(
        output_dir / "haircare_demo_product_mapping.csv",
        index=False,
        encoding="utf-8-sig",
    )
    daily_demo.to_csv(
        output_dir / "haircare_demo_daily_sales.csv", index=False, encoding="utf-8-sig"
    )
    retail_quality.to_csv(
        output_dir / "retail_cleaning_report.csv", index=False, encoding="utf-8-sig"
    )
    quality_report.to_csv(
        output_dir / "preparation_quality_report.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(quality_rows).to_csv(
        output_dir / "data_quality_audit.csv", index=False, encoding="utf-8-sig"
    )
    category_eda.to_csv(
        output_dir / "eda_category_summary.csv", index=False, encoding="utf-8-sig"
    )
    product_eda.to_csv(
        output_dir / "eda_product_summary.csv", index=False, encoding="utf-8-sig"
    )
    monthly_eda.to_csv(
        output_dir / "eda_monthly_summary.csv", index=False, encoding="utf-8-sig"
    )

    print(f"Data root: {data_root}")
    print(f"Output: {output_dir}")
    print(
        f"Retail rows: {len(transactions_clean):,}; "
        f"mapped source rows: {len(mapped_transactions):,}"
    )
    print(
        f"Products/SKUs: {mapping['product_id'].nunique()}/"
        f"{mapping['StockCode'].nunique()}; panel rows: {len(daily_demo):,}"
    )
    print(
        f"Panel totals: quantity={int(panel_totals['quantity_sold']):,}; "
        f"revenue=GBP {panel_totals['revenue_gbp']:,.2f}"
    )
    print(
        f"Unavailability in source/demo: "
        f"{int((~crawl_catalog['source_availability'].fillna(False)).sum())}/"
        f"{int((~crawl_catalog['availability']).sum())}"
    )


if __name__ == "__main__":
    main()
