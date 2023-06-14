import time
import requests
import datetime
import uuid
import random
import os
from slugify import slugify
from getpass import getpass
from typing import Dict

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
URL_API = "https://gaia-server.rosettastone.com/graphql"


def format_answers(step: dict):
    """
    Answers are different depending on the type of question
    return object: {
        "fragmented": bool,  => if the request is done one time or multiple times
        "answers": list  => list of answers
        "title": str  => title of the module
    }
    """
    if step['type'] == "card":
        # Type card can be Demo (video), Vocabulary (words), Grammar (sentence), Pronunciation (audio)
        if "additionalContent" in step["content"][0]:
            # It's the module Demo (so the video)
            return {
                "fragmented": False,
                "answers": [{"answer": answer, "correct": True} for answer in step["correct"]],
                "title": "Démonstration"
            }
        elif "carousel" in step["content"][0]:
            # It's the module Vocabulary (so the words)
            carousel = step['content'][0]['carousel']
            cards = []
            for item in carousel:
                for card in item:
                    cards.append(card)
            return {
                "fragmented": True,
                "answers": [{"answer": f"SS:{card['id']}:1:false", "correct": True} for card in cards],
                "title": "Vocabulaire"
            }
    else:
        # QCM / Organize / Fill in the blank ...
        res = {
            "fragmented": False,
            "answers": [{"answer": answer, "correct": True} for answer in step["correct"]]
        }
        if step['type'] == "multipleChoice":
            res["title"] = "Choix multiple"
        elif step['type'] == "sequencing":
            res["title"] = "Organiser"
        elif step['type'] == "cloze":
            res["title"] = "Remplissez les blancs"
        else:
            res["title"] = "Module inconnu"
        return res


def get_lesson_progress(progress, course_id, lesson_id):
    for course in progress:
        if course['courseId'] != course_id:
            continue
        for sequence in course['sequences']:
            if sequence['id'] == lesson_id:
                return sequence['percentComplete']
    # If the lesson is not found, return 0 (not even started)
    return 0


def log_course(title: str):
    print(f"\n{Colors.COURSE}{'-' * 20} {Colors.UNDERLINE}{Colors.BOLD}{title}{Colors.ENDC}{Colors.COURSE} {'-' * 20}{Colors.ENDC}")


def log_lesson(title: str):
    print(f"📚 {Colors.LESSON}{title}:{Colors.ENDC}")


def log_exercise(title: str, success: bool, total_hours: float):
    if success:
        hours, minutes = divmod(int(total_hours * 60), 60)
        str_time = f"~ {int(hours)}h {int(minutes)}min" if hours > 0 else f"{int(minutes)}min"
        print(f"  ✅ {Colors.SUCCESS}{title}{Colors.ENDC} ({str_time})")
    else:
        print(f"  ❌ {Colors.FAIL}{title}{Colors.ENDC}")


def get_activity_title(activity: dict) -> str:
    for language in activity['titles']:
        if language['locale'] == 'fr-FR':
            return language['text']


class Colors:
    COURSE = '\033[95m'
    LESSON = '\033[96m'
    SUCCESS = '\033[92m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


class RosettaStone:
    def __init__(self, hours_todo: float = 20., threshold: float = 0.2):
        # Ask for ID and password
        os.environ["RT_ID"] = input('ID (Email): ')
        os.environ["RT_PASS"] = getpass()
        try:
            self.hours_todo = float(input(f'Hours to do (default: {hours_todo}): '))
        except ValueError:
            print(f"Invalid value, using default ({hours_todo})")
            self.hours_todo = hours_todo
        self.threshold = threshold  # Threshold to consider a lesson to do (0 -> 1)
        self._authenticate()  # Set the token and user_id (self.token and self.user_id)
        self.headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Origin": "https://learn.rosettastone.com",
            "Authorization": f"Bearer {self.token}"
        }
        self.courses = self._get_courses()  # Set the courses (self.courses)
        self._calculate_hours()  # Calculate the hours to do for each lesson (self.hours_per_lesson)
        self.version = 1  # Version of the API to use (1 or 2)

        # Let's start the machine!
        for course_id, infos in self.courses.items():
            log_course(infos['title'])
            for lesson in infos['lessons']:
                log_lesson(lesson['title'])
                self._complete_lesson(course_id, lesson)

    def _authenticate(self):
        url = "https://tully.rosettastone.com/oauth/token"
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": "tully.rosettastone.com",
            "Origin": "https://tully.rosettastone.com",
        }
        data = {
            "grant_type": "password",
            "username": os.environ["RT_ID"],
            "password": os.environ["RT_PASS"],
            "client_id": "client.gaia",
        }
        rep = requests.post(url, headers=headers, data=data)
        time.sleep(1)  # Wait a bit to the style of the logs (like wow that's running)
        if rep.status_code != 200:
            print(f"❌ Error while authenticating: {rep.status_code} {rep.reason}")
            exit(1)
        print("✅ Connected to Rosetta Stone")
        rep_json = rep.json()
        self.user_id = rep_json['userId']
        self.token = rep_json['access_token']

    def _get_courses(self) -> Dict[str, Dict]:
        """
        Get the list of lessons that have not been completed
        :return: dict(str, [dict(str, str)]): value is the lesson id, key is a list of the lessons
        (id and slug) not completed
        :return: {
            "<course_id>": {
                "title": "course_title",
                "lessons": [
                    {
                        "id": "lesson_id",
                        "slug": "lesson_slug"
                    },
                    ...
                ]
            }
        }
        """
        print("🔁 Searching student courses...")
        data = {
            "operationName": "getCoursesAndProgress",
            "variables": {
                "locale": "fr-FR"
            },
            "query": "query getCoursesAndProgress($locale: String) {\n  assignedCourses {\n    ...CoursesDetails\n    "
                     "__typename\n  }\n  progress {\n    id\n    courseId\n    countOfSequencesInCourse\n    "
                     "sequences {\n      id\n      percentComplete\n      __typename\n    }\n    __typename\n  "
                     "}\n}\n\nfragment CoursesDetails on Course {\n  id\n  courseId\n  productId\n  "
                     "learningLanguage\n  title(locale: $locale)\n  cefr\n  description(locale: $locale)\n  images {"
                     "\n    ...Images\n    __typename\n  }\n  topics {\n    id\n    color\n    localizations {\n      "
                     "id\n      locale\n      text\n      __typename\n    }\n    images {\n      ...Images\n      "
                     "__typename\n    }\n    __typename\n  }\n  sequences {\n    id\n    title(locale: $locale)\n    "
                     "interaction\n    images {\n      ...Images\n      __typename\n    }\n    numberOfActivities\n   "
                     " __typename\n  }\n  __typename\n}\n\nfragment Images on ImageArray {\n  id\n  type\n  images {"
                     "\n    id\n    type\n    media_uri\n    __typename\n  }\n  __typename\n}\n "
        }
        rep = requests.post(URL_API, headers=self.headers, json=data)
        time.sleep(1)
        if rep.status_code != 200:
            print(f"❌ Error while getting courses: {rep.status_code} {rep.reason}")
            exit(1)
        print("✅ Retrieved courses")
        rep_json = rep.json()
        progress = rep_json['data']['progress']
        courses = {}
        print("🔁 Sorting the completed ones...")
        for course in rep_json['data']['assignedCourses']:
            course_id = course['courseId']
            lessons = []
            for lesson in course['sequences']:
                if get_lesson_progress(progress, course_id, lesson['id']) <= self.threshold:
                    lessons.append({
                        "id": lesson['id'],
                        "title": lesson['title'],
                        "slug": slugify(lesson['title'])
                    })
            if len(lessons) > 0:
                courses[course_id] = {
                    "title": course['title'],
                    "lessons": lessons
                }
        time.sleep(1)
        if len(courses) == 0:
            print("❌ No courses to complete")
            exit(0)
        print(f"✅ {len(courses)} courses to complete")
        return courses

    def _calculate_hours(self) -> None:
        count_lessons = 0
        for course in self.courses:
            count_lessons += len(self.courses[course]['lessons'])
        print(f"➖ Total of {count_lessons} lessons")
        self.hours_per_lesson = self.hours_todo / count_lessons

    def _get_answer(self, hours: float, data: dict) -> dict:
        """
        Get the payload to send to the server to answer the question
        :param hours: in hours (ex: 2.5)
        :param data: dict: informations for the request, like the IDs of the course, lesson etc
        :return: payload to include in the request
        """
        timestamp_now = datetime.datetime.now().isoformat() + "Z"
        return {
            "operationName": "AddProgress",
            "variables": {
                "userId": data["user_id"],
                "messages": [{
                    "userAgent": USER_AGENT,
                    "courseId": data["course_id"],
                    "sequenceId": data["sequence_id"],
                    "version": self.version,
                    "activityId": data["activity_id"],
                    "activityAttemptId": str(uuid.uuid4()),
                    "activityStepId": data["activity_step_id"],
                    "activityStepAttemptId": str(uuid.uuid4()),
                    "answers": data["answers"],
                    "score": 1,
                    "skip": False,
                    "durationMs": int(hours * 60 * 60 * 1000),
                    "endTimestamp": timestamp_now}
                ]},
            "query": "mutation AddProgress($userId: String, $messages: [ProgressMessage!]!) {\n  progress(userId: "
                     "$userId, messages: $messages) {\n    id\n    __typename\n  }\n}\n "
        }

    def _answer_success(self, response: dict):
        if 'errors' in response:
            self.version = (self.version + 1) % 2
            return False
        return True

    def _complete_step(self, course_id: str, lesson: dict, activityId: str, step: dict, hours: float)\
            -> bool:
        # Add random time to not be sus
        formatted_answers = format_answers(step)
        if formatted_answers["fragmented"]:
            success = True
            time_to_answer = hours / len(formatted_answers['answers'])
            for answer in formatted_answers["answers"]:
                data = {
                    "user_id": self.user_id,
                    "course_id": course_id,
                    "sequence_id": lesson['id'],
                    "activity_id": activityId,
                    "activity_step_id": step["activityStepId"],
                    "answers": [answer],
                }
                payload = self._get_answer(time_to_answer, data)
                rep_answer = requests.post(URL_API, headers=self.headers, json=payload)
                success = success and self._answer_success(rep_answer.json())
                time.sleep(1)
            return success
        else:
            data = {
                "user_id": self.user_id,
                "course_id": course_id,
                "sequence_id": lesson['id'],
                "activity_id": activityId,
                "activity_step_id": step["activityStepId"],
                "answers": formatted_answers["answers"],
            }
            payload = self._get_answer(hours, data)
            rep_answer = requests.post(URL_API, headers=self.headers, json=payload)
            return rep_answer.status_code == 200

    def _complete_lesson(self, course_id: str, lesson: dict):
        data = {
            "operationName": "getSequence",
            "variables": {
                "courseId": course_id,
                "sequenceSlug": lesson['slug'],
                "locale": "en-US"
            },
            "query": "query getSequence($courseId: String!, $sequenceId: String, $sequenceSlug: String, $locale: "
                     "String) {\n  sequence(courseId: $courseId, sequenceId: $sequenceId, slug: $sequenceSlug, "
                     "locale: $locale) {\n    ...SequenceDetails\n    activities\n    __typename\n  }\n}\n\nfragment "
                     "SequenceDetails on Sequence {\n  id\n  sequenceId\n  title(locale: $locale)\n  version\n  "
                     "images {\n    ...Images\n    __typename\n  }\n  lessonTopics {\n    ...LocalizableTextType\n    "
                     "__typename\n  }\n  targetedSkills\n  objectivesHeading {\n    ...LocalizableTextType\n    "
                     "__typename\n  }\n  categorizedObjectives {\n    ...CategorizedObjectiveType\n    __typename\n  "
                     "}\n  objectives {\n    id\n    localizations {\n      ...LocalizationTitle\n      __typename\n  "
                     "  }\n    __typename\n  }\n  interaction\n  __typename\n}\n\nfragment LocalizableTextType on "
                     "LocalizableText {\n  text\n  htmlText\n  localizations {\n    locale\n    text\n    htmlText\n  "
                     "  __typename\n  }\n  __typename\n}\n\nfragment LocalizationTitle on LocalizedTitle {\n  id\n  "
                     "locale\n  text\n  htmlText\n  __typename\n}\n\nfragment Images on ImageArray {\n  id\n  type\n  "
                     "images {\n    id\n    type\n    media_uri\n    __typename\n  }\n  __typename\n}\n\nfragment "
                     "CategorizedObjectiveType on CategorizedObjective {\n  id\n  objectiveTexts {\n    "
                     "...LocalizableTextType\n    __typename\n  }\n  category {\n    ...LocalizableTextType\n    "
                     "__typename\n  }\n  __typename\n}\n "
        }
        rep = requests.post(URL_API, headers=self.headers, json=data)
        rep_json = rep.json()
        activities = rep_json['data']['sequence']['activities']
        hours_per_activity = self.hours_per_lesson / len(activities)
        for activity in activities:
            # Some activities have multiple exercises
            for step in activity["steps"]:
                random_hours = hours_per_activity + hours_per_activity * random.uniform(0., 0.1)
                success = self._complete_step(course_id, lesson, activity['activityId'], step, random_hours)
                title = get_activity_title(activity)
                log_exercise(title, success, random_hours)
                time.sleep(1)
            time.sleep(5)


if __name__ == '__main__':
    RosettaStone()
