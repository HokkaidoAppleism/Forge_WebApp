import numpy as np
import random

class recommender:

    @staticmethod
    def softmax(x):
        x = np.array(x)
        e_x = np.exp(x - np.max(x))
        return e_x / e_x.sum()

    @staticmethod
    def recommend_question(user, category, cat_num_clusters, epsilon = 0.1):
        prob = random.random()
        if prob > epsilon:
            #maybe just return a cluster and diff to select from, can later add questionGetter func
            skill_scores = {cid: user.get_skill(category, cid) for cid in range(0,cat_num_clusters)}

            # 2. Compute sampling weights (weaker clusters are more likely)
            weights = recommender.softmax([1 - skill_scores[cid] for cid in range(0,cat_num_clusters)])
            chosen_cluster = np.random.choice(list(range(0, cat_num_clusters)), p=weights)

            #get diff range
            skill = user.get_skill(category, chosen_cluster)
            skill = round(skill)
            skill_range = [skill - 1, skill + 1]
            #cluster, Max and Min Skill
            # Clip to [0, 10], not [1, 10]: difficulty 0 is a real level (6002
            # questions in the database), and redo_rec already allows it. With
            # the floor at 1 the very first pick could never serve a difficulty-0
            # question while a retry could - the same skill produced two
            # different ranges depending on which path chose it.
            return chosen_cluster, np.clip(skill_range, 0, 10).tolist()
        else:
            skill = round(np.mean(user.get_random_skill()))
            skill_range = [skill - 1, skill + 1]
            return -1, np.clip(skill_range, 0, 10).tolist()

    @staticmethod
    def redo_rec(user, category, cat_num_clusters, masked_clusters, epsilon = 0.1):
        skill_scores = {cid: user.get_skill(category, cid) for cid in range(0, cat_num_clusters) if cid not in masked_clusters}

        # Every real cluster has been masked - they were all exhausted or
        # everything left was recently seen. softmax([]) over no clusters would
        # divide by a zero-size array and np.random.choice(0, ...) would raise,
        # taking the whole request down with "zero-size array". This is
        # reachable whenever a category has no more clusters than the retry
        # budget (MAX_RETRIES masks one per retry). Fall back to the same
        # explore pick recommend_question uses for its epsilon branch: cluster
        # -1, drawn off the random-skill estimate. The caller then either
        # finds a question outside the exhausted clusters or exits cleanly
        # through its own retry limit, rather than crashing here.
        remaining_ids = list(skill_scores.keys())
        if not remaining_ids:
            skill = round(np.mean(user.get_random_skill()))
            skill_range = [skill - 1, skill + 1]
            return -1, np.clip(skill_range, 0, 10).tolist()

        # 2. Compute sampling weights (weaker clusters are more likely).
        # Sample a POSITION among the remaining clusters, then map it back to
        # the real cluster id. Previously the chosen position was used as the
        # cluster id directly, so with any cluster masked it could pick a
        # masked/exhausted cluster (endless retries) and never reach the
        # higher-id remaining clusters.
        weights = recommender.softmax([1 - skill_scores[cid] for cid in remaining_ids])
        chosen_cluster = remaining_ids[np.random.choice(len(remaining_ids), p=weights)]
        # get diff range
        skill = user.get_skill(category, chosen_cluster)
        skill = round(skill)
        skill_range = [skill - 1, skill + 1]
        # cluster, Max and Min Skill
        return chosen_cluster, np.clip(skill_range, 0, 10).tolist()