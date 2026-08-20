from flask import Flask, request, jsonify
from waitress import serve
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re
import time
import uuid
import json
import threading
from datetime import datetime
from difflib import SequenceMatcher
from bs4 import BeautifulSoup

app = Flask(__name__)


ES_URL = "https://search-tmsearch-ubyuytn3nafypcaczxpjtrgmam.ap-south-1.es.amazonaws.com/v3/trade_mark"
BASE = "https://tmrsearch.ipindia.gov.in/tmrpublicsearch"
TIMEOUT = 15  # seconds; fail fast instead of hanging forever when the portal is unreachable
SESSIONS = {}  # session_id -> TrademarkSession





session = requests.Session()

adapter = HTTPAdapter(
    pool_connections=20,
    pool_maxsize=20,
    max_retries=3
)

session.mount("https://", adapter)
session.mount("http://", adapter)

def push(doc):
    try:
        r = session.post(
            ES_URL,
            json=doc,
            headers={
                "Content-Type": "application/json"
            },
            timeout=30
        )
        if r.status_code not in (200, 201):
            print(r.status_code)
            print(r.text)

    except Exception as e:
        print("Push Error:", e)







class TrademarkSession:
    def __init__(self, identifier):
        self.identifier = identifier  # phone_no or email
        self.session = requests.Session()

        # retries transient connection failures / 502-504s with exponential backoff before giving up
        retry = Retry(
            total=5,
            backoff_factor=2,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
        })
        self.tab_token = None
        self.token_name = None
        self.class_no_given = None
        self.token_id = None
        self._keepalive_stop = threading.Event()
        self.session.get(f"{BASE}/Login", timeout=TIMEOUT)

    def solve_captcha(self, question):
        # parses captcha question text and returns the numeric answer
        nums = [int(n) for n in re.findall(r"-?\d+", question)]
        q = question.lower()
        if "first number" in q:
            return nums[0]
        if "second number" in q:
            return nums[1]
        if "third number" in q:
            return nums[2]
        if "last number" in q:
            return nums[-1]
        if "evaluate the expression" in q:
            expr = question.split(":", 1)[1].strip().rstrip("= ?").strip()
            return eval(expr.replace("\u002B", "+"))
        if "what is the answer to" in q:
            expr = question.split(":", 1)[1].strip().rstrip("= ?").strip()
            return eval(expr)
        raise ValueError(f"Unrecognized captcha format: {question}")

    def send_otp(self):
        # requests OTP, auto-retrying with a fresh captcha until success=True
        while True:
            r = self.session.get(f"{BASE}/Otp/GetCaptcha", timeout=TIMEOUT)
            answer = self.solve_captcha(r.json()["question"])
            is_email = "@" in self.identifier
            payload = {
                "Email": self.identifier if is_email else None,
                "Mobile": None if is_email else self.identifier,
                "CaptchaAnswer": answer,
            }
            r = self.session.post(f"{BASE}/Otp/SendOtp", json=payload, timeout=TIMEOUT)
            data = r.json()
            if data.get("success"):
                return data
            wait = data.get("waitTime")
            if not wait:
                raise RuntimeError(data.get("message"))
            time.sleep(wait + 1)

    def verify_otp(self, otp):
        # verifies OTP; field name is OtpCode per site JS, sets tabToken cookie+header on success
        is_email = "@" in self.identifier
        payload = {
            "Email": self.identifier if is_email else None,
            "Mobile": None if is_email else self.identifier,
            "OtpCode": otp,
        }
        r = self.session.post(f"{BASE}/Otp/VerifyOtp", json=payload, timeout=TIMEOUT)
        data = r.json()
        if data.get("success"):
            self.tab_token = data["tabToken"]
            self.session.cookies.set("TabToken", self.tab_token)
            self.session.headers["Tab-Token"] = self.tab_token
            self.start_keepalive()
        return data

    def start_keepalive(self, interval=30):
        # background thread pinging KeepAlive every 30s (matches site.js setInterval) so session stays alive
        def loop():
            while not self._keepalive_stop.is_set():
                time.sleep(interval)
                try:
                    self.session.post(f"{BASE}/Otp/KeepAlive", json={"tabToken": self.tab_token}, timeout=TIMEOUT)
                except requests.RequestException:
                    pass
        threading.Thread(target=loop, daemon=True).start()

    def get_search_captcha(self):
        # post-login search page uses a different captcha endpoint than the OTP login page
        r = self.session.get(f"{BASE}/Home/GetCaptcha", timeout=TIMEOUT)
        return self.solve_captcha(r.json()["question"])

    def trademark_score(self, a, b):
        # fuzzy match percentage between the searched word and the returned wordmark
        return round(SequenceMatcher(None, a.upper(), b.upper()).ratio() * 100, 2)

    def search(self, word, class_no, search_type="phonetic"):
        # posts to Report/GetWordMarkReport and parses the returned HTML table into structured records
        captcha_answer = self.get_search_captcha()
        data = {
            "Searchstring": word,
            "Searchtype": search_type,
            "classCode": class_no,
            "CaptchaAnswer": captcha_answer,
        }
        r = self.session.post(f"{BASE}/Report/GetWordMarkReport", data=data, timeout=TIMEOUT)
        return self.parse_report(r.text, word, class_no)

    def parse_report(self, html, search_word, class_no_given):
        # builds one record dict per table row, matching the required token/field schema
        self.token_name = search_word
        self.class_no_given = class_no_given
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", id="tblwrdrpt")
        if not table:
            return []

        records = []

        for row in table.select("tbody tr"):
            cols = row.find_all("td")
            if len(cols) < 11:
                continue

            # column order: 0=checkbox/sno, 1=appl no, 2=class, 3=appl date, 4=wordmark,
            # 5=proprietor, 6=used since, 7=valid upto, 8=status, 9=image, 10=description
            multi_class = row.find("a", string=lambda x: x and "Multi Class" in x)

            img = cols[9].find("img")
            image = img.get("src", "") if img else None
            has_image = bool(img)

            application_no = cols[1].get_text(strip=True)
            class_span = cols[2].find("span")
            class_no = class_span.get_text(strip=True) if class_span else cols[2].get_text(strip=True)
            application_date = cols[3].get_text(strip=True)
            wordmark = cols[4].get_text(strip=True)
            proprietor_name = cols[5].get_text(" ", strip=True)
            used_since = cols[6].get_text(strip=True)
            valid_upto = cols[7].get_text(strip=True)
            status = cols[8].get_text(" ", strip=True)
            description = cols[10].get_text(" ", strip=True)

            phonetic_value = re.sub(r"[^A-Za-z0-9]", "", wordmark).upper()
            datey = datetime.now().strftime("%Y%m%d")
            given_brandname = self.token_name.replace(" ", "")
            ref_id = f"{datey}_{given_brandname}_{self.class_no_given}"
            self.token_id = ref_id

            try:
                application_date_iso = datetime.strptime(application_date, "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                application_date_iso = application_date  # keep raw text (e.g. "---") if not a real date

            records.append({
                "tokenid": ref_id,
                "search_by_text": search_word,
                "search_by_text_nospace": re.sub(r"[^A-Za-z0-9]", "", search_word).upper(),
                "tm_applied_for": wordmark,
                "tm_applied_for_upper_nospace": phonetic_value,
                "wordmark": wordmark,
                "wordmark_nospace": phonetic_value,
                "match_percentage": self.trademark_score(search_word, wordmark),
                "proprietor_name": proprietor_name.replace(".", "").replace(",", "").replace(" ", "").strip(),
                "proprietor_name_live": proprietor_name,
                "application_no": application_no,
                "class": class_no,
                "multi_class": bool(multi_class),
                "status": status,
                "logo": image,
                "has_image": has_image,
                "application_date": application_date_iso,
                "used_since": used_since,
                "valid_upto": valid_upto,
                "goods_service_details": description,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

        records.sort(key=lambda x: x["match_percentage"], reverse=True)
        print("Total Result:", len(records))
        return records

    def generate_report(self, app_no, report_date, mail_id, mail_name, eng_id):

        url = "https://global.indiaapis.com/mca/generate_report"

        payload = {
            "token": self.token_id,
            "app_no": app_no,
            "report_date": report_date,
            "to_mail": mail_id,
            "to_name": mail_name,
            "engagement_id": eng_id
        }

        print("*" * 20)
        print(json.dumps(payload, indent=2))
        print("*" * 20)

        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )

        response.raise_for_status()

        j = response.json()

        email_data = json.loads(j["email_response"]["response"])
        report_id = email_data["message"]["otherParams"]["id"]

        print("Report ID:", report_id)

        return {
        "report_id": report_id,
        "response": j  # or whatever you actually want to expose
        }

    def logout(self):
        # stops the keepalive thread and notifies the portal so the server-side session closes cleanly
        self._keepalive_stop.set()
        try:
            self.session.post(f"{BASE}/Otp/CloseTab", json={"tabToken": self.tab_token}, timeout=TIMEOUT)
        except requests.RequestException:
            pass

