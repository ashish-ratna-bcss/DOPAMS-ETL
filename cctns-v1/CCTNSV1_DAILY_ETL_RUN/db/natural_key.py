"""Compute upsert keys in Python (must match db/sql/004 triggers). Used for batch dedupe before load."""


def _norm(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.replace("\x00", "").strip()
    return str(value).strip()


def _fields(rec: dict) -> dict:
    return {k.upper(): v for k, v in rec.items()}


def record_key(entity: str, rec: dict) -> str:
    ru = _fields(rec)
    if entity == "fir":
        return _norm(ru.get("FIR_REG_NUM"))
    if entity == "court":
        return "|".join(
            [
                _norm(ru.get("FIR_REG_NUM")),
                _norm(ru.get("CHARGESHEET_DT")),
                _norm(ru.get("COURT_DISPOSAL_DT")),
                _norm(ru.get("COURT_CASE_NUM")),
                _norm(ru.get("COURT_NAME")),
                _norm(ru.get("COURT_DISPOSAL_TYPE")),
                _norm(ru.get("COURT_REMARKS")),
            ]
        )
    if entity == "accused_details":
        return "|".join(
            [
                _norm(ru.get("FIR_REG_NUM")),
                _norm(ru.get("PERSON_CODE")),
                _norm(ru.get("ACCUSED_NAME")),
                _norm(ru.get("GENDER")),
                _norm(ru.get("AGE")),
                _norm(ru.get("FATHER_NAME")),
                _norm(ru.get("MOBILE_1")),
                _norm(ru.get("ACCUSED_PRESENT_ADDRESS")),
                _norm(ru.get("ACCUSED_PERMANENT_ADDRESS")),
                _norm(ru.get("IS_ARRESTED")),
                _norm(ru.get("ARREST_SURRENDER_DT")),
            ]
        )
    if entity == "accused":
        return "|".join(
            [
                _norm(ru.get("FIR_REG_NUM")),
                _norm(ru.get("ACCUSED_NAME")),
                _norm(ru.get("FATHER_NAME")),
                _norm(ru.get("DOB")),
                _norm(ru.get("MOBILE_1")),
                _norm(ru.get("GENDER")),
                _norm(ru.get("AGE")),
                _norm(ru.get("PRESENT_ADDRESS")),
                _norm(ru.get("PERMANENT_ADDRESS")),
                _norm(ru.get("FROM_DT")),
                _norm(ru.get("TO_DT")),
                _norm(ru.get("ARREST_SURRENDER_DT")),
            ]
        )
    raise ValueError(f"unknown entity for record_key: {entity}")


def fir_reg_num(rec: dict) -> str:
    return _norm(_fields(rec).get("FIR_REG_NUM"))
