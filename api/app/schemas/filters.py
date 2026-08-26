from datetime import date


# Query services implement inclusive date_to as a half-open next-day bound.
# Keeping one day below date.max makes that conversion representable.
MAX_FILTER_DATE = date(9998, 12, 31)
