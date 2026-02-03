import aiohttp
import json
from datetime import datetime

class LibrusAPI:
    def __init__(self, cookies=None):
        self.host = "https://synergia.librus.pl/gateway/api/2.0/"
        self.cookies = cookies
        
    async def login(self, login: str, password: str) -> dict:
        """
        Authenticate with Librus and return session cookies.
        Based on librusik implementation.
        """
        try:
            async with aiohttp.ClientSession() as session:
                # Step 1: Initialize OAuth
                await session.get(
                    "https://api.librus.pl/OAuth/Authorization?client_id=46&response_type=code&scope=mydata"
                )
                
                # Step 2: Login with credentials
                form = aiohttp.FormData()
                form.add_field("action", "login")
                form.add_field("login", login)
                form.add_field("pass", password)
                
                resp = await session.post(
                    "https://api.librus.pl/OAuth/Authorization?client_id=46",
                    data=form
                )
                
                # Check for login error
                text = await resp.text()
                if "Nieprawidłowy login" in text or resp.status == 401:
                    return {"success": False, "error": "Nieprawidłowy login lub hasło"}
                
                # Step 3: Grant access
                resp = await session.get(
                    "https://api.librus.pl/OAuth/Authorization/Grant?client_id=46"
                )
                
                if resp.status != 200:
                    return {"success": False, "error": "Grant failed"}
                
                # Get cookies from session
                cookies = session.cookie_jar.filter_cookies("https://synergia.librus.pl")
                self.cookies = {k: v.value for k, v in cookies.items()}
                
                # Step 4: Activate API access
                activated = await self._activate_api_access(session)
                
                if not activated:
                    return {"success": False, "error": "API activation failed"}
                
                # Verify by getting /Me
                me = await self._get_data_with_session(session, "Me")
                if me:
                    user_info = me.get("Me", {}).get("Account", {})
                    return {
                        "success": True,
                        "cookies": self.cookies,
                        "user": {
                            "firstName": user_info.get("FirstName"),
                            "lastName": user_info.get("LastName"),
                            "login": user_info.get("Login")
                        }
                    }
                
                return {"success": False, "error": "Could not verify login"}
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _activate_api_access(self, session) -> bool:
        """Activate API access by calling TokenInfo and UserInfo endpoints."""
        try:
            cookies = session.cookie_jar.filter_cookies("https://synergia.librus.pl")
            
            async with session.get(self.host + "Auth/TokenInfo", timeout=10) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                identifier = data.get("UserIdentifier")
            
            if identifier:
                async with session.get(f"{self.host}Auth/UserInfo/{identifier}", timeout=10) as resp:
                    return resp.status == 200
            
            return False
        except:
            return False
    
    async def _get_data_with_session(self, session, method: str):
        """Get data from API using existing session."""
        try:
            async with session.get(self.host + method, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
        except:
            pass
        return None
    
    async def get_data(self, method: str):
        """Get data from Librus API."""
        if not self.cookies:
            return None
        
        try:
            async with aiohttp.ClientSession(cookies=self.cookies) as session:
                async with session.get(self.host + method, timeout=10) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 401:
                        return {"error": "session_expired"}
        except Exception as e:
            return {"error": str(e)}
        return None
    
    async def get_me(self):
        """Get current user info."""
        data = await self.get_data("Me")
        if data and "Me" in data:
            return data["Me"]["Account"]
        return None
    
    async def get_subjects(self):
        """Get all subjects."""
        data = await self.get_data("Subjects")
        if data and "Subjects" in data:
            return {x["Id"]: x["Name"] for x in data["Subjects"]}
        return {}
    
    async def get_teachers(self):
        """Get all teachers."""
        data = await self.get_data("Users")
        if data and "Users" in data:
            return {
                x["Id"]: {
                    "FirstName": x.get("FirstName", ""),
                    "LastName": x.get("LastName", "")
                } for x in data["Users"]
            }
        return {}
    
    async def get_lessons(self):
        """Get lessons mapping."""
        data = await self.get_data("Lessons")
        if data and "Lessons" in data:
            return {x["Id"]: x["Subject"]["Id"] for x in data["Lessons"]}
        return {}
    
    async def get_attendance_types(self):
        """Get attendance types."""
        data = await self.get_data("Attendances/Types")
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
        attendances_data = await self.get_data("Attendances")
        if not attendances_data or "Attendances" not in attendances_data:
            if attendances_data and "error" in attendances_data:
                return attendances_data
            return {"error": "no_data"}
        
        # Get supporting data
        subjects = await self.get_subjects()
        teachers = await self.get_teachers()
        lessons = await self.get_lessons()
        types = await self.get_attendance_types()
        
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
        
        return {
            "attendances": result,
            "stats": stats,
            "percentage": percentage,
            "total": total,
            "bySubject": subjects_list
        }
    
    async def get_grades(self):
        """Get all grades."""
        grades_data = await self.get_data("Grades")
        if not grades_data or "Grades" not in grades_data:
            if grades_data and "error" in grades_data:
                return grades_data
            return {"error": "no_data"}
        
        subjects = await self.get_subjects()
        teachers = await self.get_teachers()
        
        # Get categories
        categories_data = await self.get_data("Grades/Categories")
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
