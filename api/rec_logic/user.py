from collections import deque

import numpy as np

class user:
    def __init__(self, reported_skill):
        self.user_data = {}
        self.reported_skill = reported_skill
        self.recently_seen = deque(maxlen=5)
        self.random_skill = reported_skill
        self.questions_answered = 0
        self.correct_answers = 0
        # session_start_time was removed: it was initialised to None here and
        # never assigned anywhere, so get_session_stats() reported a null every
        # time. The real start time lives on the session dict in merged_api
        # (user_sessions[sid]['start_time']), which is what /getUserStats
        # actually returns, so this only ever added a misleading always-null
        # field to the payload.
        #self.user_data['Visual Fine Arts']

    def update_skill(self, user_skill, question_difficulty, correct,
                     K=1.5, S=2.0, celerity=0):
        # Bug 2 fix. `celerity` is the fraction of the tossup still unread at the
        # buzz (1.0 = first word, ~0 = last line). The old formula only let
        # celerity scale the *size* of a move, never its sign, so converting a
        # question on its very last word still raised skill. Now we compare the
        # player's actual buzz point against the buzz point a player at this
        # skill "should" convert this difficulty at (`expected_celerity`): buzz
        # earlier than expected -> skill goes up, later -> it goes down.
        # S controls how sharply that crossover shifts with the skill/difficulty
        # gap; K is the same move-size knob as before, retuned upward because
        # (actual - expected_celerity) has a smaller natural range than the old
        # (1 + celerity) multiplier did.
        expected_celerity = 1 / (1 + np.exp(-(user_skill - question_difficulty) / S))
        actual = celerity if correct else 0.0
        new_skill = user_skill + K * (actual - expected_celerity)
        return max(0.0, min(10.0, new_skill))  # Clamp between 0–10

    def update_stats(self, qid, category, cluster_id, correct, question_difficulty, celerity):
        self.questions_answered += 1
        if correct:
            self.correct_answers += 1
        if category not in self.user_data:
            self.user_data[category] = {}
        if cluster_id not in self.user_data[category] and cluster_id != -1:
            # Seed a brand-new cluster at the player's running mean skill, not
            # the frozen reported_skill -- same reasoning as get_skill below.
            self.user_data[category][cluster_id] = self.overall_skill()
        if cluster_id == -1:
            self.random_skill = self.update_skill(self.random_skill, question_difficulty, correct, celerity=celerity)
            self.recently_seen.append(qid)
            return
        #add something that incudes speed into this also tweak how fast this happens -- maybe *0.5
        current_skill = self.user_data[category][cluster_id]
        new_skill = self.update_skill(current_skill, question_difficulty, correct, celerity=celerity)
        self.user_data[category][cluster_id] = new_skill
        self.recently_seen.append(qid)

    def overall_skill(self):
        """Running mean skill across every cluster that has any data.

        Bug 1 fix / display metric. Used both as the fallback for a cluster the
        player has never answered (so the recommender serves it near where the
        player actually sits, not frozen at the session's starting skill) and
        as the single "Current Skill" number shown to the player, which
        otherwise jumped around with whichever cluster the current question
        happened to belong to. Same shape as the end_difficulty calculation in
        routes/adaptive.py's /end handler.
        """
        vals = [s for clusters in self.user_data.values()
                for s in clusters.values()]
        return sum(vals) / len(vals) if vals else self.reported_skill

    def get_skill(self, category, cluster_id):
        cluster = self.user_data.get(category, {})
        if cluster_id in cluster:
            return cluster[cluster_id]
        # No answer recorded for this cluster yet: fall back to the mean of the
        # clusters that do have data rather than the frozen reported_skill. The
        # first question of a fresh session still gets reported_skill, because
        # overall_skill() has nothing to average yet.
        return self.overall_skill()

    def get_random_skill(self):
        return self.random_skill

    def restore_stats(self, user_data):
        """Reload per-cluster skill saved from an earlier session.

        Cluster ids are ints in memory but come back from JSON as strings, so
        they are coerced - without this the restored data never matches a
        lookup and the session silently starts from scratch.
        """
        if not isinstance(user_data, dict):
            return
        restored = {}
        for category, clusters in user_data.items():
            if not isinstance(clusters, dict):
                continue
            bucket = {}
            for cluster_id, skill in clusters.items():
                try:
                    bucket[int(cluster_id)] = float(skill)
                except (TypeError, ValueError):
                    continue          # skip anything unparseable rather than fail the resume
            restored[category] = bucket
        self.user_data = restored

    def get_stats(self):
        return self.user_data

    def get_session_stats(self, category):
        if category not in self.user_data or not self.user_data[category]:
            return {
                'questions_answered': self.questions_answered,
                'correct_answers': self.correct_answers,
                'current_difficulty': self.reported_skill,
                'start_difficulty': self.reported_skill,
            }
        print(self.user_data[category].values())
        cluster_skills = self.user_data[category].values()
        current_difficulty = np.mean(list(cluster_skills)) if cluster_skills else self.reported_skill

        return {
            'questions_answered': self.questions_answered,
            'correct_answers': self.correct_answers,
            'current_difficulty': current_difficulty,
            'start_difficulty': self.reported_skill,
        }

    def get_recently_seen(self):
        return self.recently_seen