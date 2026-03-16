import aiohttp
import asyncio
import logging
import time


logger = logging.getLogger(__name__)

class LibrusAPI:
    def __init__(self, cookies=None, trace_id: str | None = None):
        self.host = "https://synergia.librus.pl/gateway/api/2.0/"
        self.cookies = cookies
        self.trace_id = trace_id or "librus"
        self.login_timeout = aiohttp.ClientTimeout(total=15, connect=10, sock_connect=10, sock_read=10)
        self.data_timeout = aiohttp.ClientTimeout(total=12, connect=8, sock_connect=8, sock_read=8)
        self.oauth_init_timeout = aiohttp.ClientTimeout(total=25, connect=12, sock_connect=12, sock_read=20)
        self.default_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        self.oauth_headers = {
            **self.default_headers,
            "Referer": "https://portal.librus.pl/",
            "Origin": "https://portal.librus.pl",
        }

    def _log(self, level: int, message: str, *args, **kwargs) -> None:
        logger.log(level, "[%s] " + message, self.trace_id, *args, **kwargs)

    def _mask_login(self, login: str) -> str:
        if len(login) <= 2:
            return "*" * len(login)
        hidden = max(len(login) - 4, 1)
        return f"{login[:2]}{'*' * hidden}{login[-2:]}"

    async def _initialize_oauth(self, session: aiohttp.ClientSession) -> dict | None:
        url = "https://api.librus.pl/OAuth/Authorization?client_id=46&response_type=code&scope=mydata"

        for attempt in range(1, 3):
            try:
                self._log(logging.INFO, "Login step 1/5: initialize OAuth (attempt %s/2)", attempt)
                async with session.get(url, headers=self.oauth_headers, timeout=self.oauth_init_timeout) as resp:
                    await resp.read()
                    if resp.status >= 500:
                        self._log(logging.WARNING, "OAuth init failed with status %s", resp.status)
                        return {"success": False, "error": "Librus jest chwilowo niedostepny", "code": "upstream_unavailable"}
                    if resp.status >= 400:
                        self._log(logging.WARNING, "OAuth init rejected with status %s", resp.status)
                        return {"success": False, "error": "Librus odrzucil rozpoczecie logowania", "code": "oauth_init_failed"}
                    return None
            except asyncio.TimeoutError:
                self._log(logging.WARNING, "OAuth init attempt %s timed out", attempt)
                if attempt == 2:
                    return {
                        "success": False,
                        "error": "Librus odpowiadal zbyt dlugo juz przy rozpoczeciu logowania.",
                        "code": "timeout"
                    }
                await asyncio.sleep(1)

        return {
            "success": False,
            "error": "Nie udalo sie rozpoczec logowania do Librusa.",
            "code": "oauth_init_failed"
        }
        
    async def login(self, login: str, password: str) -> dict:
        """
        Authenticate with Librus and return session cookies.
        Based on librusik implementation.
        """
        started_at = time.monotonic()
        self._log(logging.INFO, "Starting login flow for %s", self._mask_login(login))

        try:
            async with aiohttp.ClientSession(timeout=self.login_timeout, headers=self.default_headers) as session:
                # Step 1: Initialize OAuth
                init_result = await self._initialize_oauth(session)
                if init_result:
                    return init_result
                
                # Step 2: Login with credentials
                self._log(logging.INFO, "Login step 2/5: submit credentials")
                form = aiohttp.FormData()
                form.add_field("action", "login")
                form.add_field("login", login)
                form.add_field("pass", password)
                
                async with session.post(
                    "https://api.librus.pl/OAuth/Authorization?client_id=46",
                    data=form,
                    headers={
                        **self.oauth_headers,
                        "Content-Type": "application/x-www-form-urlencoded"
                    }
                ) as resp:
                    text = await resp.text()
                    if "Nieprawidłowy login" in text or resp.status == 401:
                        self._log(logging.INFO, "Login rejected by Librus")
                        return {"success": False, "error": "Nieprawidlowy login lub haslo", "code": "invalid_credentials"}
                    if resp.status >= 500:
                        self._log(logging.WARNING, "Credential submit failed with status %s", resp.status)
                        return {"success": False, "error": "Librus jest chwilowo niedostepny", "code": "upstream_unavailable"}
                
                # Step 3: Grant access
                self._log(logging.INFO, "Login step 3/5: grant access")
                async with session.get(
                    "https://api.librus.pl/OAuth/Authorization/Grant?client_id=46",
                    headers=self.oauth_headers
                ) as resp:
                    if resp.status != 200:
                        body = (await resp.text())[:200]
                        self._log(logging.WARNING, "Grant failed with status %s body=%r", resp.status, body)
                        return {
                            "success": False,
                            "error": "Nie udalo sie dokonczyc logowania w Librusie",
                            "code": "grant_failed"
                        }
                    await resp.read()
                
                # Get cookies from session
                cookies = session.cookie_jar.filter_cookies("https://synergia.librus.pl")
                self.cookies = {k: v.value for k, v in cookies.items()}
                self._log(logging.INFO, "Login step 4/5: activate API access")
                
                # Step 4: Activate API access
                activated = await self._activate_api_access(session)
                
                if not activated:
                    return {
                        "success": False,
                        "error": "Nie udalo sie aktywowac dostepu do API Librusa",
                        "code": "activation_failed"
                    }
                
                # Verify by getting /Me
                self._log(logging.INFO, "Login step 5/5: verify session")
                me = await self._get_data_with_session(session, "Me")
                if me and "Me" in me:
                    user_info = me.get("Me", {}).get("Account", {})
                    self._log(logging.INFO, "Login flow finished in %.2fs", time.monotonic() - started_at)
                    return {
                        "success": True,
                        "cookies": self.cookies,
                        "user": {
                            "firstName": user_info.get("FirstName"),
                            "lastName": user_info.get("LastName"),
                            "login": user_info.get("Login")
                        }
                    }
                if me and me.get("error") == "request_timeout":
                    return {
                        "success": False,
                        "error": "Librus odpowiadal zbyt dlugo podczas potwierdzania sesji",
                        "code": "timeout"
                    }
                
                return {
                    "success": False,
                    "error": "Nie udalo sie potwierdzic sesji Librusa",
                    "code": "login_verification_failed"
                }
                
        except asyncio.TimeoutError:
            self._log(logging.WARNING, "Login flow timed out after %.2fs", time.monotonic() - started_at)
            return {
                "success": False,
                "error": "Librus odpowiadal zbyt dlugo. Sprobuj ponownie za chwile.",
                "code": "timeout"
            }
        except aiohttp.ClientError as e:
            self._log(logging.ERROR, "Login flow client error: %s", e)
            return {
                "success": False,
                "error": "Nie udalo sie polaczyc z Librusem.",
                "code": "connection_error"
            }
        except Exception as e:
            self._log(logging.ERROR, "Unexpected login error: %s", e, exc_info=True)
            return {
                "success": False,
                "error": "Wystapil wewnetrzny blad logowania do Librusa.",
                "code": "internal_error"
            }
    
    async def _activate_api_access(self, session) -> bool:
        """Activate API access by calling TokenInfo and UserInfo endpoints."""
        try:
            self._log(logging.INFO, "Activation step 1/2: Auth/TokenInfo")
            data = await self._get_data_with_session(session, "Auth/TokenInfo")
            if not data or data.get("error"):
                self._log(logging.WARNING, "TokenInfo failed: %s", data.get("error") if isinstance(data, dict) else "no_data")
                return False

            identifier = data.get("UserIdentifier")
            
            if identifier:
                self._log(logging.INFO, "Activation step 2/2: Auth/UserInfo/%s", identifier)
                user_info = await self._get_data_with_session(session, f"Auth/UserInfo/{identifier}")
                if user_info and not user_info.get("error"):
                    return True
                self._log(
                    logging.WARNING,
                    "UserInfo activation failed: %s",
                    user_info.get("error") if isinstance(user_info, dict) else "no_data"
                )
                return False
            
            self._log(logging.WARNING, "TokenInfo did not return UserIdentifier")
            return False
        except Exception:
            self._log(logging.ERROR, "Unexpected activation error", exc_info=True)
            return False
    
    async def _get_data_with_session(self, session, method: str):
        """Get data from API using existing session."""
        try:
            async with session.get(self.host + method, timeout=self.data_timeout) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                if resp.status == 401:
                    self._log(logging.INFO, "Request %s returned 401", method)
                    return {"error": "session_expired"}

                body = (await resp.text())[:200]
                self._log(logging.WARNING, "Request %s failed with status %s body=%r", method, resp.status, body)
                if resp.status >= 500:
                    return {"error": "upstream_unavailable"}
                return {"error": f"request_failed_{resp.status}"}
        except asyncio.TimeoutError:
            self._log(logging.WARNING, "Request %s timed out", method)
            return {"error": "request_timeout"}
        except aiohttp.ClientError as e:
            self._log(logging.ERROR, "Request %s client error: %s", method, e)
            return {"error": "connection_error"}
        except Exception:
            self._log(logging.ERROR, "Unexpected request error for %s", method, exc_info=True)
            return {"error": "internal_error"}
    
    async def get_data(self, method: str, session: aiohttp.ClientSession | None = None):
        """Get data from Librus API."""
        if not self.cookies:
            return {"error": "session_missing"}
        
        if session is not None:
            return await self._get_data_with_session(session, method)

        async with aiohttp.ClientSession(cookies=self.cookies, timeout=self.data_timeout) as new_session:
            return await self._get_data_with_session(new_session, method)
    
    async def get_me(self, session: aiohttp.ClientSession | None = None):
        """Get current user info."""
        data = await self.get_data("Me", session=session)
        if data and "Me" in data:
            return data["Me"]["Account"]
        return None
    
    async def get_subjects(self, session: aiohttp.ClientSession | None = None):
        """Get all subjects."""
        data = await self.get_data("Subjects", session=session)
        if data and "Subjects" in data:
            return {x["Id"]: x["Name"] for x in data["Subjects"]}
        return {}
    
    async def get_teachers(self, session: aiohttp.ClientSession | None = None):
        """Get all teachers."""
        data = await self.get_data("Users", session=session)
        if data and "Users" in data:
            return {
                x["Id"]: {
                    "FirstName": x.get("FirstName", ""),
                    "LastName": x.get("LastName", "")
                } for x in data["Users"]
            }
        return {}
    
    async def get_lessons(self, session: aiohttp.ClientSession | None = None):
        """Get lessons mapping."""
        data = await self.get_data("Lessons", session=session)
        if data and "Lessons" in data:
            return {x["Id"]: x["Subject"]["Id"] for x in data["Lessons"]}
        return {}
    
    async def get_attendance_types(self, session: aiohttp.ClientSession | None = None):
        """Get attendance types."""
        data = await self.get_data("Attendances/Types", session=session)
        if data and "Types" in data:
            return {
                x["Id"]: {
                    "isPresence": x.get("IsPresenceKind", False),
                    "name": x.get("Name", ""),
                    "short": x.get("Short", "")
                } for x in data["Types"]
            }
        return {}
    
    async def get_attendances(self):
        """Get all attendances with full details."""
        started_at = time.monotonic()
        self._log(logging.INFO, "Fetching attendances")

        async with aiohttp.ClientSession(cookies=self.cookies, timeout=self.data_timeout) as session:
            attendances_data = await self.get_data("Attendances", session=session)
            if not attendances_data or "Attendances" not in attendances_data:
                if attendances_data and "error" in attendances_data:
                    return attendances_data
                return {"error": "no_data"}

            subjects, teachers, lessons, types = await asyncio.gather(
                self.get_subjects(session=session),
                self.get_teachers(session=session),
                self.get_lessons(session=session),
                self.get_attendance_types(session=session)
            )
        
        result = []
        stats = {
            "present": 0,
            "absent": 0,
            "late": 0,
            "excused": 0,
            "other": 0
        }
        
        # Per-subject stats
        by_subject = {}
        
        for att in attendances_data["Attendances"]:
            type_id = att.get("Type", {}).get("Id")
            lesson_id = att.get("Lesson", {}).get("Id")
            teacher_id = att.get("AddedBy", {}).get("Id")
            
            att_type = types.get(type_id, {})
            subject_id = lessons.get(lesson_id)
            subject_name = subjects.get(subject_id, "Nieznany")
            teacher = teachers.get(teacher_id, {"FirstName": "", "LastName": ""})
            
            # Initialize subject stats if not exists
            if subject_name not in by_subject:
                by_subject[subject_name] = {"present": 0, "absent": 0, "late": 0, "excused": 0}
            
            # Categorize
            short = att_type.get("short", "").lower()
            is_presence = att_type.get("isPresence", False)
            
            if short == "sp" or "spóźn" in att_type.get("name", "").lower():
                stats["late"] += 1
                by_subject[subject_name]["late"] += 1
                category = "late"
            elif is_presence or short == "ob":
                stats["present"] += 1
                by_subject[subject_name]["present"] += 1
                category = "present"
            elif short in ["u", "nu", "us"]:
                stats["excused"] += 1
                by_subject[subject_name]["excused"] += 1
                category = "excused"
            elif short == "nb":
                stats["absent"] += 1
                by_subject[subject_name]["absent"] += 1
                category = "absent"
            else:
                stats["other"] += 1
                category = "other"
            
            result.append({
                "date": att.get("Date"),
                "subject": subject_name,
                "type": att_type.get("name", ""),
                "short": att_type.get("short", ""),
                "category": category,
                "semester": att.get("Semester", 1),
                "teacher": f"{teacher.get('FirstName', '')} {teacher.get('LastName', '')}".strip()
            })
        
        # Calculate percentage - present AND late counts (excused are still absences)
        total = stats["present"] + stats["absent"] + stats["late"] + stats["excused"]
        percentage = 0
        if total > 0:
            percentage = round((stats["present"] + stats["late"]) / total * 100)
        
        # Build per-subject list with percentages
        subjects_list = []
        for subj_name, subj_stats in sorted(by_subject.items()):
            subj_total = subj_stats["present"] + subj_stats["absent"] + subj_stats["late"] + subj_stats["excused"]
            # Present AND late counts as attendance
            subj_pct = round((subj_stats["present"] + subj_stats["late"]) / subj_total * 100) if subj_total > 0 else 100
            
            subjects_list.append({
                "name": subj_name,
                "present": subj_stats["present"],
                "excused": subj_stats["excused"],
                "absent": subj_stats["absent"],
                "late": subj_stats["late"],
                "percentage": subj_pct
            })
        
        # Sort by percentage descending (best first)
        subjects_list.sort(key=lambda x: x["percentage"], reverse=True)
        self._log(logging.INFO, "Attendances fetched in %.2fs", time.monotonic() - started_at)
        
        return {
            "attendances": result,
            "stats": stats,
            "percentage": percentage,
            "total": total,
            "bySubject": subjects_list
        }
    
    async def get_grades(self):
        """Get all grades."""
        started_at = time.monotonic()
        self._log(logging.INFO, "Fetching grades")

        async with aiohttp.ClientSession(cookies=self.cookies, timeout=self.data_timeout) as session:
            grades_data, subjects, teachers, categories_data = await asyncio.gather(
                self.get_data("Grades", session=session),
                self.get_subjects(session=session),
                self.get_teachers(session=session),
                self.get_data("Grades/Categories", session=session)
            )

        if not grades_data or "Grades" not in grades_data:
            if grades_data and "error" in grades_data:
                return grades_data
            return {"error": "no_data"}

        categories = {}
        if categories_data and "Categories" in categories_data:
            for cat in categories_data["Categories"]:
                categories[cat["Id"]] = {
                    "name": cat.get("Name", ""),
                    "weight": cat.get("Weight", 0)
                }
        
        result = {}
        for grade in grades_data["Grades"]:
            subject_id = grade.get("Subject", {}).get("Id")
            subject_name = subjects.get(subject_id, "Nieznany")
            
            if subject_name not in result:
                result[subject_name] = []
            
            category_id = grade.get("Category", {}).get("Id")
            category = categories.get(category_id, {})
            teacher_id = grade.get("AddedBy", {}).get("Id")
            teacher = teachers.get(teacher_id, {"FirstName": "", "LastName": ""})
            
            result[subject_name].append({
                "grade": grade.get("Grade"),
                "weight": category.get("weight", 0),
                "category": category.get("name", ""),
                "date": grade.get("Date"),
                "addDate": grade.get("AddDate"),
                "semester": grade.get("Semester"),
                "isFinal": grade.get("IsFinal", False) or grade.get("IsFinalProposition", False),
                "isSemester": grade.get("IsSemester", False) or grade.get("IsSemesterProposition", False),
                "teacher": f"{teacher.get('FirstName', '')} {teacher.get('LastName', '')}".strip()
            })

        self._log(logging.INFO, "Grades fetched in %.2fs", time.monotonic() - started_at)
        return {"grades": result}
