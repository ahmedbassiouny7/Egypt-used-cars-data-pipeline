import argparse
import re

import pandas as pd
from psycopg2.extras import execute_values

from db_utils import get_connection


GOVERNORATE_KEYWORDS = {
    "cairo": ["cairo", "new cairo", "tagamo3", "nasr city", "heliopolis", "maadi", "mokattam", "shorouk", "madinaty", "rehab"],
    "giza": ["giza", "6th of october", "october", "sheikh zayed", "zayed", "haram", "faisal", "dokki", "mohandessin"],
    "alexandria": ["alex", "alexandria"],
    "dakahlia": ["dakahlia", "mansoura"],
    "red sea": ["red sea", "hurghada", "gouna"],
    "beheira": ["beheira", "damanhur"],
    "fayoum": ["fayoum", "faiyum"],
    "gharbia": ["gharbia", "tanta", "mahalla"],
    "ismailia": ["ismailia"],
    "monufia": ["monufia", "menofia", "shebin", "shibin", "ashmun", "ashmoun", "bagour", "tala", "quwaysna"],
    "minya": ["minya"],
    "qalyubia": ["qalyubia", "qalubia", "benha", "shubra el kheima"],
    "new valley": ["new valley", "wadi gedid", "kharga", "farafra"],
    "suez": ["suez"],
    "aswan": ["aswan", "kom ombo"],
    "assiut": ["assiut", "assyut", "asyut"],
    "beni suef": ["beni suef", "ben suef"],
    "port said": ["port said", "portsaid"],
    "damietta": ["damietta", "domyat"],
    "sharkia": ["sharkia", "sharqia", "zagazig", "faqous", "bilbeis", "abu kabir", "dyarb negm"],
    "south sinai": ["south sinai", "sharm", "dahab"],
    "north sinai": ["north sinai", "arish"],
    "kafr el sheikh": ["kafr el sheikh"],
    "matrouh": ["matrouh", "marsa matrouh"],
    "luxor": ["luxor"],
    "qena": ["qena"],
    "sohag": ["sohag"],
}

# Cleaning guardrails. Prices below MIN_VALID_PRICE are treated as missing/bad;
# prices above MAX_REASONABLE_PRICE are treated as likely scrape or entry errors.
MIN_VALID_PRICE = 100000
MAX_REASONABLE_PRICE = 50000000
REFERENCE_LOW_MULTIPLIER = 0.5
REFERENCE_HIGH_MULTIPLIER = 1.5
IQR_MIN_GROUP_SIZE = 5


def clean_location(location):
    if pd.isna(location):
        return pd.NA
    location = str(location).lower().strip()
    location = re.sub(r"[^a-z0-9,\s]", " ", location)
    location = re.sub(r"\s+", " ", location)
    return location


def match_governorate(text):
    for governorate, keywords in GOVERNORATE_KEYWORDS.items():
        for keyword in sorted(keywords, key=len, reverse=True):
            if re.search(rf"\b{re.escape(keyword)}\b", text):
                return governorate
    return pd.NA


def get_governorate(location):
    if pd.isna(location):
        return pd.NA

    # Check the last comma-separated part first because locations are usually
    # written as "city, governorate". This avoids matching city names as the
    # governorate when both are known words.
    parts = [part.strip() for part in str(location).split(",") if part.strip()]
    for part in reversed(parts):
        governorate = match_governorate(part)
        if not pd.isna(governorate):
            return governorate

    return match_governorate(str(location))


def standardize_text_columns(df):
    text_columns = df.select_dtypes(include="object").columns
    for column in text_columns:
        df[column] = df[column].astype("string").str.strip()
        df[column] = df[column].replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
    return df


def read_latest_raw_rows(batch_id=None):
    # Airflow passes a batch_id when this DAG is triggered by the scrape DAG.
    # Manual runs leave it empty and transform all raw rows not already loaded.
    where_clause = ""
    params = None
    if batch_id:
        where_clause = "WHERE batch_id = %s"
        params = (batch_id,)

    query = f"""
        SELECT
            car_hash,
            batch_id,
            car_name,
            year,
            mileage,
            transmission,
            fuel,
            price,
            location,
            brand,
            model,
            car_url,
            scraped_at
        FROM raw_used_cars
        {where_clause}
        ORDER BY loaded_at DESC
    """

    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def normalize_match_key(value):
    # Reference prices and listing text do not always use identical casing or
    # punctuation, so both sides are normalized before joining.
    if pd.isna(value):
        return pd.NA
    value = str(value).lower().strip()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value or pd.NA


def read_price_reference():
    # Keep the newest reference row for each brand/model/year combination.
    query = """
        SELECT DISTINCT ON (LOWER(brand), LOWER(model), model_year)
            brand,
            model,
            model_year,
            average_price,
            minimum_price,
            maximum_price,
            scraped_at
        FROM used_car_price_reference
        WHERE average_price IS NOT NULL
        ORDER BY LOWER(brand), LOWER(model), model_year, loaded_at DESC
    """
    try:
        with get_connection() as conn:
            reference_df = pd.read_sql_query(query, conn)
    except Exception as exc:
        print(f"Price reference table is unavailable, using scraped-data averages only. Details: {exc}")
        return pd.DataFrame()

    if reference_df.empty:
        return reference_df

    reference_df["brand_key"] = reference_df["brand"].apply(normalize_match_key)
    reference_df["model_key"] = reference_df["model"].apply(normalize_match_key)
    reference_df["year"] = pd.to_numeric(reference_df["model_year"], errors="coerce").astype("Int64")
    reference_df["reference_average_price"] = pd.to_numeric(reference_df["average_price"], errors="coerce")
    reference_df["reference_minimum_price"] = pd.to_numeric(reference_df["minimum_price"], errors="coerce")
    reference_df["reference_maximum_price"] = pd.to_numeric(reference_df["maximum_price"], errors="coerce")
    return reference_df[
        [
            "brand_key",
            "model_key",
            "year",
            "reference_average_price",
            "reference_minimum_price",
            "reference_maximum_price",
        ]
    ]


def clean_used_cars(df):
    df = standardize_text_columns(df)

    # Raw prices and mileage include labels such as "EGP" and "KM"; only digits
    # are kept before numeric conversion.
    df["price"] = pd.to_numeric(
        df["price"].astype("string").str.replace(r"\D", "", regex=True),
        errors="coerce",
    ).astype("Float64")
    df["mileage"] = pd.to_numeric(
        df["mileage"].astype("string").str.replace(r"\D", "", regex=True),
        errors="coerce",
    )
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    df["scraped_at"] = pd.to_datetime(df["scraped_at"], errors="coerce")
    apply_price_imputation(df, read_price_reference())

    df["location_clean"] = df["location"].apply(clean_location)
    df["city"] = df["location_clean"].str.split(",").str[0].str.strip()
    df["governorate"] = df["location_clean"].apply(get_governorate)
    df["city"] = df["city"].replace("", pd.NA)
    df = df.drop(columns=["location_clean"])

    df = df.where(pd.notna(df), None)
    cleaned_rows = df.to_dict("records")
    for row in cleaned_rows:
        for key, value in row.items():
            row[key] = to_db_value(value)
    return cleaned_rows


def apply_price_imputation(df, reference_df=None):
    df["price_original"] = df["price"]
    df["price_was_imputed"] = False
    df["price_imputation_level"] = None

    invalid_prices = df["price"].isna() | (df["price"] < MIN_VALID_PRICE)

    # First use Hatla2ee's own used-price reference table when an exact
    # brand/model/year reference exists. This handles missing prices and clear
    # low/high outliers before relying on averages from scraped listings.
    if reference_df is not None and not reference_df.empty:
        df["brand_key"] = df["brand"].apply(normalize_match_key)
        df["model_key"] = df["model"].apply(normalize_match_key)
        df["_row_order"] = range(len(df))

        df_with_reference = df.merge(
            reference_df,
            on=["brand_key", "model_key", "year"],
            how="left",
            sort=False,
        ).sort_values("_row_order")

        reference_fill = df_with_reference["reference_average_price"].reset_index(drop=True)
        reference_min = df_with_reference["reference_minimum_price"].reset_index(drop=True)
        reference_max = df_with_reference["reference_maximum_price"].reset_index(drop=True)
        price_values = df["price"].reset_index(drop=True)
        has_reference = reference_fill.notna()

        reference_missing_mask = invalid_prices.reset_index(drop=True) & has_reference
        reference_low_mask = (
            price_values.notna()
            & reference_min.notna()
            & (price_values < reference_min * REFERENCE_LOW_MULTIPLIER)
            & has_reference
        )
        reference_high_mask = (
            price_values.notna()
            & reference_max.notna()
            & (price_values > reference_max * REFERENCE_HIGH_MULTIPLIER)
            & has_reference
        )

        reference_rules = [
            (reference_missing_mask, "reference_missing_price"),
            (reference_low_mask, "reference_low_outlier"),
            (reference_high_mask, "reference_high_outlier"),
        ]
        for mask, level in reference_rules:
            mask_values = mask.values
            df.loc[mask_values, "price"] = reference_fill[mask].values
            df.loc[mask_values, "price_was_imputed"] = True
            df.loc[mask_values, "price_imputation_level"] = level

        df.drop(columns=["brand_key", "model_key", "_row_order"], inplace=True, errors="ignore")

    apply_iqr_price_outliers(df, ["brand", "model", "year"], "iqr_model_year_median")
    apply_iqr_price_outliers(df, ["brand", "model"], "iqr_model_median")
    apply_iqr_price_outliers(df, ["brand"], "iqr_brand_median")
    apply_absolute_price_cap(df)

    # Remaining invalid prices are filled from the most specific available
    # average: model first, then brand, then a global fallback.
    valid_prices = (
        df["price"].notna()
        & (df["price"] >= MIN_VALID_PRICE)
        & (df["price"] <= MAX_REASONABLE_PRICE)
        & ~df["price_was_imputed"]
    )
    invalid_prices = df["price"].isna() | (df["price"] < MIN_VALID_PRICE)

    model_avg = df.loc[valid_prices].groupby("model")["price"].mean()
    brand_avg = df.loc[valid_prices].groupby("brand")["price"].mean()
    global_avg = df.loc[valid_prices, "price"].mean()

    model_fill = df["model"].map(model_avg)
    model_mask = invalid_prices & model_fill.notna()
    df.loc[model_mask, "price"] = model_fill[model_mask]
    df.loc[model_mask, "price_was_imputed"] = True
    df.loc[model_mask, "price_imputation_level"] = "model_avg"

    still_invalid = df["price"].isna() | (df["price"] < MIN_VALID_PRICE)
    brand_fill = df["brand"].map(brand_avg)
    brand_mask = still_invalid & brand_fill.notna()
    df.loc[brand_mask, "price"] = brand_fill[brand_mask]
    df.loc[brand_mask, "price_was_imputed"] = True
    df.loc[brand_mask, "price_imputation_level"] = "brand_avg"

    still_invalid = df["price"].isna() | (df["price"] < MIN_VALID_PRICE)
    if pd.notna(global_avg):
        df.loc[still_invalid, "price"] = global_avg
        df.loc[still_invalid, "price_was_imputed"] = True
        df.loc[still_invalid, "price_imputation_level"] = "global_avg"


def apply_iqr_price_outliers(df, group_columns, imputation_level):
    # IQR outlier replacement is only applied to groups with enough observations;
    # tiny groups are skipped because their quartiles are too unstable.
    valid_base = (
        df["price"].notna()
        & (df["price"] >= MIN_VALID_PRICE)
        & (df["price"] <= MAX_REASONABLE_PRICE)
        & ~df["price_was_imputed"]
    )
    stats_source = df.loc[valid_base, [*group_columns, "price"]].copy()
    if stats_source.empty:
        return

    group_stats = (
        stats_source.groupby(group_columns, dropna=False)["price"]
        .agg(
            group_count="count",
            q1=lambda series: series.quantile(0.25),
            q3=lambda series: series.quantile(0.75),
            median_price="median",
        )
        .reset_index()
    )
    group_stats = group_stats[group_stats["group_count"] >= IQR_MIN_GROUP_SIZE]
    if group_stats.empty:
        return

    group_stats["iqr"] = group_stats["q3"] - group_stats["q1"]
    group_stats = group_stats[group_stats["iqr"] > 0]
    if group_stats.empty:
        return

    group_stats["lower_bound"] = group_stats["q1"] - 1.5 * group_stats["iqr"]
    group_stats["upper_bound"] = group_stats["q3"] + 1.5 * group_stats["iqr"]

    df["_row_order"] = range(len(df))
    df_with_stats = df.merge(group_stats, on=group_columns, how="left", sort=False).sort_values("_row_order")

    price_values = df_with_stats["price"].reset_index(drop=True)
    lower_bound = df_with_stats["lower_bound"].reset_index(drop=True)
    upper_bound = df_with_stats["upper_bound"].reset_index(drop=True)
    median_fill = df_with_stats["median_price"].reset_index(drop=True)
    already_imputed = df["price_was_imputed"].reset_index(drop=True)

    iqr_mask = (
        ~already_imputed
        & price_values.notna()
        & lower_bound.notna()
        & upper_bound.notna()
        & ((price_values < lower_bound) | (price_values > upper_bound))
    )
    mask_values = iqr_mask.values
    df.loc[mask_values, "price"] = median_fill[iqr_mask].values
    df.loc[mask_values, "price_was_imputed"] = True
    df.loc[mask_values, "price_imputation_level"] = imputation_level
    df.drop(columns=["_row_order"], inplace=True, errors="ignore")


def apply_absolute_price_cap(df):
    # Extremely high values can distort averages and dashboard axes. Replace
    # them with a brand median when possible, otherwise a global median.
    valid_source = (
        df["price"].notna()
        & (df["price"] >= MIN_VALID_PRICE)
        & (df["price"] <= MAX_REASONABLE_PRICE)
        & ~df["price_was_imputed"]
    )
    brand_median = df.loc[valid_source].groupby("brand")["price"].median()
    global_median = df.loc[valid_source, "price"].median()

    high_mask = (
        df["price"].notna()
        & (df["price"] > MAX_REASONABLE_PRICE)
        & ~df["price_was_imputed"]
    )
    if not high_mask.any():
        return

    brand_fill = df["brand"].map(brand_median)
    brand_cap_mask = high_mask & brand_fill.notna()
    df.loc[brand_cap_mask, "price"] = brand_fill[brand_cap_mask]
    df.loc[brand_cap_mask, "price_was_imputed"] = True
    df.loc[brand_cap_mask, "price_imputation_level"] = "absolute_high_brand_median"

    remaining_high_mask = (
        df["price"].notna()
        & (df["price"] > MAX_REASONABLE_PRICE)
        & ~df["price_was_imputed"]
    )
    if remaining_high_mask.any() and pd.notna(global_median):
        df.loc[remaining_high_mask, "price"] = global_median
        df.loc[remaining_high_mask, "price_was_imputed"] = True
        df.loc[remaining_high_mask, "price_imputation_level"] = "absolute_high_global_median"


def to_db_value(value):
    # psycopg2 handles native Python values more reliably than pandas scalar
    # objects, especially nullable integer/float/timestamp dtypes.
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if hasattr(value, "item"):
        return value.item()
    return value


def insert_cleaned_rows(rows):
    if not rows:
        return 0

    columns = [
        "car_hash",
        "car_name",
        "year",
        "mileage",
        "transmission",
        "fuel",
        "price_original",
        "price",
        "price_was_imputed",
        "price_imputation_level",
        "location",
        "city",
        "governorate",
        "brand",
        "model",
        "car_url",
        "scraped_at",
        "batch_id",
    ]
    values = [[row.get(column) for column in columns] for row in rows]

    query = f"""
        INSERT INTO cleaned_used_cars ({", ".join(columns)})
        VALUES %s
        -- Cleaned rows use the same car_hash key as raw rows.
        ON CONFLICT (car_hash) DO NOTHING
        RETURNING car_hash
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            inserted_rows = execute_values(cursor, query, values, fetch=True)
    return len(inserted_rows)


def transform_raw_to_cleaned(batch_id=None):
    raw_df = read_latest_raw_rows(batch_id=batch_id)
    if raw_df.empty:
        print("No raw rows found in raw_used_cars. Run the scrape DAG first.")
        return 0
    cleaned_rows = clean_used_cars(raw_df)
    return insert_cleaned_rows(cleaned_rows)


def main():
    parser = argparse.ArgumentParser(description="Transform raw Postgres used-car data into cleaned Postgres data.")
    parser.add_argument("--batch-id", default=None)
    args = parser.parse_args()
    batch_id = args.batch_id or None

    row_count = transform_raw_to_cleaned(batch_id=batch_id)
    print(f"Inserted {row_count} new cleaned rows into cleaned_used_cars")


if __name__ == "__main__":
    main()
