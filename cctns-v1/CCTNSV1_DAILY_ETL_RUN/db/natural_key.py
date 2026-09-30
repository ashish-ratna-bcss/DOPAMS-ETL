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
        cols = [
            "FIR_REG_NUM", "DISTRICT", "PS", "FIR_NO", "REG_DT", "YEAR", "FIR_STATUS", "ACT_SEC",
            "MAJOR_HEAD", "MINOR_HEAD", "FROM_DT", "TO_DT", "PS_RECV_INFORM_DT", "DRUG_PARTICULARS",
            "WEIGHT_GM", "DRUG_DESC", "DRUG_STATUS", "DRUG_TYPE", "ESTIMATED_VALUE", "AREA_OPERATION",
            "LOCATION_TYPE", "DRUG_PLACE_TYPE", "PAKING_MAKING_DESC", "PACKETS_COUNT", "ACCUSED_NAME",
            "AGE", "FATHER_NAME", "ACCUSED_OCCUPATION", "GENDER", "CASTE", "NATIONALITY",
            "TELEPHONE_RESIDENCE", "ALIAS_NAME", "DOB", "MOBILE_1", "EMAIL", "SOCIAL_MEDIA_ACCNT",
            "AADHAR_CARD", "RATION_CARD", "VOTER_CARD", "PASSPORT", "PAN_CARD", "ELECTRICITY_CONNECTION",
            "TELEPHONE_CONNECTION", "GAS_CONNECTION", "DRIVING_LICENSE", "OTHER_PROOFS", "PRESENT_ADDRESS",
            "PERMANENT_ADDRESS", "ARREST_SURRENDER_DT", "BUILD_TYPE", "COMPLEXION_TYPE", "HEIGHT_LL_FEET",
            "HEIGHT_FROM_CM", "FACE_TYPE", "LIPS_TYPE", "NOSE_TYPE", "CHEEK_TYPE", "TEETH_TYPE",
            "BEARD_TYPE", "EYE_TYPE", "EYE_BROW_THICKNESS", "EYE_BLIND", "EYE_COLOR", "LEGS_MISSING",
            "TOE_EXTRA", "EARS_MISSING", "TOE_MISSING", "DEAF_DUMB", "ARMS_MISSING", "HAIR_COLOR",
            "HAIR_STYLE", "WEIGHT_KG", "EARS_TYPE_CD", "OTHER_IDENTIFY_MARKS", "ARREST_PS",
            "MODUS_OPERANDI", "HISTORYSHEET_Y_N", "PHOTO_Y_N", "INT_RELATION_TYPE_FATHER",
            "INT_FATHER_NAME", "INT_FATHER_MOBILE_NO", "INT_FATHER_OCCUPATION", "INT_FATHER_ADDRESS",
            "INT_RELATION_TYPE_MOTHER", "INT_MOTHER_NAME", "INT_MOTHER_MOBILE_NO", "INT_MOTHER_OCCUPATION",
            "INT_MOTHER_ADDRESS", "INT_RELATION_TYPE_WIFE", "INT_WIFE_NAME", "INT_WIFE_MOBILE_NO",
            "INT_WIFE_OCCUPATION", "INT_WIFE_ADDRESS", "INT_RELATION_TYPE_SON", "INT_SON_NAME",
            "INT_SON_MOBILE_NO", "INT_SON_OCCUPATION", "INT_SON_ADDRESS", "INT_RELATION_TYPE_DAUGHTER",
            "INT_DAUGHTER_NAME", "INT_DAUGHTER_MOBILE_NO", "INT_DAUGHTER_OCCUPATION", "INT_DAUGHTER_ADDRESS",
            "INT_RELATION_TYPE_BROTHER", "INT_BROTHER_NAME", "INT_BROTHER_MOBILE_NO", "INT_BROTHER_OCCUPATION",
            "INT_BROTHER_ADDRESS", "INT_RELATION_TYPE_SISTER", "INT_SISTER_NAME", "INT_SISTER_MOBILE_NO",
            "INT_SISTER_OCCUPATION", "INT_SISTER_ADDRESS", "INT_RELATION_TYPE_FIL", "INT_FIL_NAME",
            "INT_FIL_MOBILE_NO", "INT_FIL_OCCUPATION", "INT_FIL_ADDRESS", "INT_RELATION_TYPE_MIL",
            "INT_MIL_NAME", "INT_MIL_MOBILE_NO", "INT_MIL_OCCUPATION", "INT_MIL_ADDRESS",
            "INT_RELATION_TYPE_UNCLE", "INT_UNCLE_NAME", "INT_UNCLE_MOBILE_NO", "INT_UNCLE_OCCUPATION",
            "INT_UNCLE_ADDRESS", "INT_RELATION_TYPE_AUNT", "INT_AUNT_NAME", "INT_AUNT_MOBILE_NO",
            "INT_AUNT_OCCUPATION", "INT_AUNT_ADDRESS", "INT_RELATION_TYPE_FRIEND", "INT_FRIEND_NAME",
            "INT_FRIEND_MOBILE_NO", "INT_FRIEND_OCCUPATION", "INT_FRIEND_ADDRESS", "FIR_CONTENTS"
        ]
        import hashlib
        payload_str = "|".join(_norm(ru.get(c)) for c in cols)
        return hashlib.md5(payload_str.encode("utf-8")).hexdigest()
    raise ValueError(f"unknown entity for record_key: {entity}")


def fir_reg_num(rec: dict) -> str:
    return _norm(_fields(rec).get("FIR_REG_NUM"))
