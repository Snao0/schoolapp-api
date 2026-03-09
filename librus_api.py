import requests

class LibrusAPI:
    def __init__(self, cookies=None):
        self.host = "https://synergia.librus.pl/gateway/api/2.0/"
        self.cookies = cookies or {}

    def login(self, login: str, password: str) -> dict:
        """
        Authenticate with Librus and return session cookies.
        Uses synchronous requests (compatible with gunicorn sync workers).
        """
        try:
            session = requests.Session()
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
            })

            # Step 1: Initialize OAuth
            session.get(
                "https://api.librus.pl/OAuth/Authorization?client_id=46&response_type=code&scope=mydata",
                allow_redirects=True,
                timeout=15
            )

            # Step 2: Login with credentials
            resp = session.post(
                "https://api.librus.pl/OAuth/Authorization?client_id=46",
                data={
                    "action": "login",
                    "login": login,
                    "pass": password
                },
                allow_redirects=False,
                timeout=15
            )

            # Check for login error
            if "Nieprawidłowy login" in resp.text or resp.status_code == 401:
                return {"success": False, "error": "Nieprawidłowy login lub hasło"}

            # Step 3: Grant access - follow redirects to Synergia
            resp = session.get(
                "https://api.librus.pl/OAuth/Authorization/Grant?client_id=46",
                allow_redirects=True,
                timeout=15
            )

            # Extract cookies for Synergia
            self.cookies = dict(session.cookies)

            # Step 4: Verify by getting /Me
            me_resp = session.get(
                self.host + "Me",
                timeout=10
            )

            if me_resp.status_code == 200:
                try:
                    me_data = me_resp.json()
                    user_info = me_data.get("Me", {}).get("Account", {})
                    return {
                        "success": True,
                        "cookies": self.cookies,
                        "user": {
                            "firstName": user_info.get("FirstName"),
                            "lastName": user_info.get("LastName"),
                            "login": user_info.get("Login")
                        }
                    }
                except Exception:
                    pass

            return {"success": False, "error": "Błąd autoryzacji API Librus"}

        except requests.exceptions.Timeout:
            return {"success": False, "error": "Timeout - serwer Librus nie odpowiada"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_data(self, method: str):
        """Get data from Librus API."""
        if not self.cookies:
            return None

        try:
            resp = requests.get(
                self.host + method,
                cookies=self.cookies,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'application/json'
                },
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 401:
                return {"error": "session_expired"}
        except Exception as e:
            return {"error": str(e)}
        return None

    def get_me(self):
        """Get current user info."""
        data = self.get_data("Me")
        if data and "Me" in data:
            return data["Me"]["Account"]
        return None

    def get_subjects(self):
        """Get all subjects."""
        data = self.get_data("Subjects")
        if data and "Subjects" in data:
            return {x["Id"]: x["Name"] for x in data["Subjects"]}
        return {}

    def get_teachers(self):
        """Get all teachers."""
        data = self.get_data("Users")
        if data and "Users" in data:
            return {
                x["Id"]: {
                    "FirstName": x.get("FirstName", ""),
                    "LastName": x.get("LastName", "")
                } for x in data["Users"]
            }
        return {}

    def get_lessons(self):
        """Get lessons mapping."""
        data = self.get_data("Lessons")
        if data and "Lessons" in data:
            return {x["Id"]: x["Subject"]["Id"] for x in data["Lessons"]}
        return {}

    def get_attendance_types(self):
        """Get attendance types."""
        data = self.get_data("Attendances/Types")
        if data and "Types" in data:
            return {
                x["Id"]: {
                    "isPresence": x.get("IsPresenceKind", False),
                    "name": x.get("Name", ""),
                    "short": x.get("Short", "")
                } for x in data["Types"]
            }
        return {}

    def get_attendances(self):
        """Get all attendances with full details."""
        attendances_data = self.get_data("Attendances")
        if not attendances_data or "Attendances" not in attendances_data:
            if attendances_data and "error" in attendances_data:
                return attendances_data
            return {"error": "no_data"}

        # Get supporting data
        subjects = self.get_subjects()
        teachers = self.get_teachers()
        lessons = self.get_lessons()
        types = self.get_attendance_types()

        result = []
        stats = {
            "present": 0,
            "absent": 0,
            "late": 0,
            "excused": 0,
            "other": 0
        }
        by_subject = {}

        for att in attendances_data["Attendances"]:
            type_id = att.get("Type", {}).get("Id")
            lesson_id = att.get("Lesson", {}).get("Id")
            teacher_id = att.get("AddedBy", {}).get("Id")

            att_type = types.get(type_id, {})
            subject_id = lessons.get(lesson_id)
            subject_name = subjects.get(subject_id, "Nieznany")
            teacher = teachers.get(teacher_id, {"FirstName": "", "LastName": ""})

            if subject_name not in by_subject:
                by_subject[subject_name] = {"present": 0, "absent": 0, "late": 0, "excused": 0}

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

        total = stats["present"] + stats["absent"] + stats["late"] + stats["excused"]
        percentage = 0
        if total > 0:
            percentage = round((stats["present"] + stats["late"]) / total * 100)

        subjects_list = []
        for subj_name, subj_stats in sorted(by_subject.items()):
            subj_total = subj_stats["present"] + subj_stats["absent"] + subj_stats["late"] + subj_stats["excused"]
            subj_pct = round((subj_stats["present"] + subj_stats["late"]) / subj_total * 100) if subj_total > 0 else 100

            subjects_list.append({
                "name": subj_name,
                "present": subj_stats["present"],
                "excused": subj_stats["excused"],
                "absent": subj_stats["absent"],
                "late": subj_stats["late"],
                "percentage": subj_pct
            })

        subjects_list.sort(key=lambda x: x["percentage"], reverse=True)

        return {
            "attendances": result,
            "stats": stats,
            "percentage": percentage,
            "total": total,
            "bySubject": subjects_list
        }

    def get_grades(self):
        """Get all grades."""
        grades_data = self.get_data("Grades")
        if not grades_data or "Grades" not in grades_data:
            if grades_data and "error" in grades_data:
                return grades_data
            return {"error": "no_data"}

        subjects = self.get_subjects()
        teachers = self.get_teachers()

        # Get categories
        categories_data = self.get_data("Grades/Categories")
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

        return {"grades": result}
