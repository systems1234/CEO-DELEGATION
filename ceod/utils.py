from __future__ import annotations

import random
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from ceod.exceptions import ValidationError
from ceod.models import SeedProfile

DONE_REPLY_RE = re.compile(
  r"\b(done|completed|finish|finished|ho gaya|ho gya|complete|kar diya|kardiya|yes)\b",
  re.IGNORECASE,
)
POSTPONE_REPLY_RE = re.compile(
  r"\b(postpone|delay|extend|nahi|nahin|not done|pending|baad mein|later|reschedule|no)\b",
  re.IGNORECASE,
)
ROW_ID_RE = re.compile(r"\bT\d{13}[0-9A-F]{4}\b")
DATE_FLEXIBLE_RE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$")


def timezone_now(timezone_name: str) -> datetime:
  return datetime.now(tz=ZoneInfo(timezone_name))


def today_iso(timezone_name: str) -> str:
  return timezone_now(timezone_name).strftime("%Y-%m-%d")


def format_date_for_display(iso_date: str | None) -> str:
  if not iso_date:
    return "N/A"
  parts = iso_date.split("-")
  if len(parts) != 3:
    return iso_date
  return f"{parts[2]}/{parts[1]}/{parts[0]}"


def normalise_whatsapp_number(raw_value: str | None) -> str:
  digits = re.sub(r"\D", "", raw_value or "")
  if not digits:
    raise ValidationError("WhatsApp number is empty")
  if not re.fullmatch(r"\d{10,15}", digits):
    raise ValidationError(f"Invalid WhatsApp number format: {raw_value!r}")
  return digits


def parse_date_flexible(value: str | None) -> str | None:
  if not value:
    return None
  match = DATE_FLEXIBLE_RE.match(value.strip())
  if not match:
    return None
  day, month, year = [int(part) for part in match.groups()]
  if year < 100:
    year += 2000
  return f"{year:04d}-{month:02d}-{day:02d}"


def extract_row_id(text: str) -> str | None:
  match = ROW_ID_RE.search(text)
  return match.group(0) if match else None


def is_done_reply(text: str) -> bool:
  return bool(DONE_REPLY_RE.search(text))


def is_postpone_reply(text: str) -> bool:
  return bool(POSTPONE_REPLY_RE.search(text))


def generate_row_id() -> str:
  return f"T{int(time.time() * 1000)}{random.randint(0, 65535):04X}"


def build_random_seed_profiles(
  count: int,
  existing_names: set[str],
  existing_numbers: set[str],
  existing_sheet_names: set[str],
) -> list[SeedProfile]:
  first_names = [
    "Aarav", "Vivaan", "Aditya", "Krish", "Ishaan",
    "Diya", "Anaya", "Sara", "Myra", "Kiara",
    "Rahul", "Priya", "Neha", "Rohan", "Kunal",
    "Aditi", "Meera", "Arjun", "Sanya", "Nikhil",
  ]
  last_names = [
    "Sharma", "Verma", "Gupta", "Patel", "Singh",
    "Yadav", "Kapoor", "Bansal", "Agarwal", "Mehta",
    "Reddy", "Nair", "Iyer", "Malhotra", "Jain",
  ]

  profiles: list[SeedProfile] = []
  attempts = 0
  while len(profiles) < count:
    attempts += 1
    if attempts > 1000:
      raise ValidationError("Unable to generate enough unique test profiles")

    first_name = random.choice(first_names)
    last_name = random.choice(last_names)
    suffix = f"{random.randint(100, 999)}"
    name = f"{first_name} {last_name} {suffix}"
    sheet_name = f"{first_name}_{suffix}"
    number = generate_random_indian_mobile(existing_numbers)

    lower_name = name.lower()
    lower_sheet_name = sheet_name.lower()
    if lower_name in existing_names or lower_sheet_name in existing_sheet_names:
      continue

    existing_names.add(lower_name)
    existing_numbers.add(number)
    existing_sheet_names.add(lower_sheet_name)
    profiles.append(SeedProfile(name=name, number=number, sheet_name=sheet_name))

  return profiles


def generate_random_indian_mobile(existing_numbers: set[str]) -> str:
  while True:
    local_number = f"{random.randint(6, 9)}"
    while len(local_number) < 10:
      local_number += str(random.randint(0, 9))
    full_number = f"91{local_number}"
    if full_number not in existing_numbers:
      return full_number

