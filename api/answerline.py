"""Reading a packet answerline: what it says, and what counts as saying it.

DUPLICATED FROM forge_backend/merged_api.py, and that is a problem to fix
before this merges -- see web/README.md. Two copies of a matcher this fiddly
will drift, and the drift will show up as the web build scoring an answer
differently from the desktop one. The right shape is a shared
forge_backend/answerline.py that both import; it is left as a copy here only so
this baseline can be reviewed without editing files on the desktop branch.

The logic itself is unmodified. Its history is worth knowing before touching
it: the original was a substring test that accepted "a" for "Gaius Julius
Caesar", and the rewrite after that accepted the halves of an answerline that
explicitly forbade them -- 3,330 answerlines accepted something they reject,
and 20,862 accepted directive text like "prompt on purgatory" as an answer.
Both were caught by measuring against the real question set, not by reading.
"""

import re

_ANSWER_STOPWORDS = {
    'a', 'an', 'the', 'of', 'in', 'on', 'at', 'to', 'or', 'and', 'is', 'it',
    'this', 'that', 'his', 'her', 'its', 'prompt', 'accept', 'answer',
}

# Everything from here on in a clause describes what is NOT a full answer.
# Matched anywhere, not just at the start: packets overwhelmingly write one
# combined group - "[or Russian Empire; do not accept "Soviet Union" or
# "USSR"]" - so anchoring at the start of the *group* let the forbidden half
# through as accepted alternates.
_NEGATIVE_DIRECTIVE = re.compile(
    r"\b(?:do\s+not\s+accept|do\s+not|don'?t\s+accept|don'?t|reject|"
    r"anti-?prompt|prompt)\b", re.I)

# What separates one clause of an answerline group from the next: a semicolon,
# or a comma that introduces a fresh "or ..." alternate.
_CLAUSE_SPLIT = re.compile(r';|,\s*(?=or\s)', re.I)

# Prose the writer wraps around an example, rather than an answer to type.
_FORM_PROSE_PREFIX = re.compile(
    r"^(?:answers?|things?|alternatives?|equivalents?|synonyms?|obvious\s+synonyms?|"
    r"word\s+forms?)\s+(?:like|such\s+as|for|of)\s+", re.I)

# Filler that is never itself the answer.
_FILLER_FORMS = {
    'etc', 'eg', 'ie', 'other', 'others', 'answers', 'answer', 'alternatives',
    'alternative', 'equivalents', 'equivalent', 'anything', 'similar',
    'synonyms', 'synonym', 'word forms', 'these', 'those', 'it', 'them',
}

# Moderator directives that belong on a packet, not on a heading.
_DIRECTIVE_WORDS = re.compile(
    r'\b(?:prompt|anti-?prompt|accept|reject|require|or|do not|don\'t|'
    r'word forms|equivalents?|answers? (?:like|mentioning|involving))\b', re.I)

# Pronunciation help is worth keeping - it is how you say the answer.
_PRONUNCIATION = re.compile(r'\b(?:pron\.?|pronounced|pronunciation)\b', re.I)


def clean_answerline(text):
    """Reduce a packet answerline to just the answer.

    Square brackets in quizbowl are always directives, so they go unless they
    carry pronunciation. Parentheses serve both purposes, so those go only when
    they read as an instruction -- "Symphony No. 7 (Leningrad)" stays.

    Groups are matched by depth rather than by regex so nested ones are
    handled: "Gilbert Newton Lewis (accept (hard/soft) Lewis acids)" has an
    inner pair a flat pattern cannot see past.
    """
    if not text:
        return ''
    original = str(text).strip()

    # Packet writers sign an answerline with their initials in angle brackets -
    # "Set [accept Seth or Suetekh] <Peter>". Left in, "<Peter>" became part of
    # the primary acceptable form, so "Set" stopped matching and "Peter" -- the
    # editor, not an answer -- started. 1,331 answerlines carry one.
    original = re.sub(r'\s*<[^<>]*>\s*', ' ', original).strip()

    def strip_groups(source, opener, closer, drop):
        out, depth, start = [], 0, 0
        for i, ch in enumerate(source):
            if ch == opener:
                if depth == 0:
                    start = i
                depth += 1
            elif ch == closer and depth > 0:
                depth -= 1
                if depth == 0:
                    group = source[start:i + 1]
                    if not drop(group):
                        out.append(group)
            elif depth == 0:
                out.append(ch)
        if depth > 0:                      # unbalanced - keep the remainder
            out.append(source[start:])
        return ''.join(out)

    cleaned = strip_groups(original, '[', ']',
                           lambda g: not _PRONUNCIATION.search(g))
    cleaned = strip_groups(
        cleaned, '(', ')',
        lambda g: not _PRONUNCIATION.search(g) and bool(_DIRECTIVE_WORDS.search(g)))
    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()
    cleaned = cleaned.strip(' ,;:')
    cleaned = cleaned.strip('"“”‘’\'').strip()
    # Never return nothing - a weird answerline beats a blank heading.
    return cleaned or original


def normalize_answer(text):
    """Lowercase, drop punctuation, collapse spaces."""
    return re.sub(r'\s+', ' ',
                  re.sub(r'[^0-9a-z\s]', ' ', str(text or '').lower())).strip()


def acceptable_answer_forms(correct):
    """Every string that should count as this answer.

    Alternates from "[or X]" and "[accept X]" are real answers. Directives
    marking something as *not* sufficient -- "[prompt on X]", "[do not accept
    X]", "[reject X]" -- are skipped.
    """
    text = str(correct or '')
    forms = [clean_answerline(text)]

    depth, start = 0, 0
    for i, ch in enumerate(text):
        if ch in '[(':
            if depth == 0:
                start = i
            depth += 1
        elif ch in '])' and depth > 0:
            depth -= 1
            if depth == 0:
                inner = text[start + 1:i].strip()
                # Semicolons always start a fresh thought, so each segment is
                # judged on its own. A comma-then-"or" only sometimes does,
                # which is handled inside the segment where its context is
                # still known.
                for segment in inner.split(';'):
                    # `poisoned` carries a directive across the comma-or
                    # continuations it governs. Two lookalike shapes:
                    #   "(prompt on the member tribes: Mohawk, ..., or
                    #    Tuscarora)" - the whole list is forbidden;
                    #   "[accept Seleucia-Ctesiphon but prompt if answer does
                    #    not give "Ctesiphon", or al-Mada'in or Mahoze]" - an
                    #    aside, after which the accept list resumes.
                    # What separates them is whether anything survived in front
                    # of the directive.
                    poisoned = False
                    for clause in _CLAUSE_SPLIT.split(segment):
                        clause = (clause or '').strip()
                        if not clause:
                            continue
                        negative = _NEGATIVE_DIRECTIVE.search(clause)
                        if negative:
                            clause = clause[:negative.start()].strip()
                            # Truncation can leave the conjunction that
                            # introduced the aside ("Seleucia-Ctesiphon but").
                            clause = re.sub(r'\b(?:but|and|or|,)\s*$', '', clause).strip()
                            poisoned = not clause
                            if not clause:
                                continue
                        elif poisoned:
                            continue
                        clause = re.sub(r'^(?:or|also accept|accept)\b\s*', '',
                                        clause, flags=re.I)
                        clause = _FORM_PROSE_PREFIX.sub('', clause)
                        for part in re.split(r',| or ', clause):
                            part = part.strip(' "\'')
                            part = re.sub(r'\b(?:before|until|after)\b.*$', '',
                                          part, flags=re.I).strip()
                            if part and normalize_answer(part) not in _FILLER_FORMS:
                                forms.append(part)

    seen, out = set(), []
    for form in forms:
        norm = normalize_answer(form)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def simple_answer_match(guess, correct):
    """Offline answer check, used when the qbreader API is unreachable."""
    if not guess or not str(guess).strip() or not correct:
        return False

    g = normalize_answer(guess)
    if not g or g in _ANSWER_STOPWORDS:
        return False
    guess_tokens = g.split()

    for form in acceptable_answer_forms(correct):
        if g == form:
            return True
        form_tokens = form.split()
        if not form_tokens:
            continue
        # "Caesar" for "Gaius Julius Caesar": names the head word and adds
        # nothing that isn't in the answer.
        if (guess_tokens[-1] == form_tokens[-1]
                and len(form_tokens[-1]) >= 3
                and set(guess_tokens) <= set(form_tokens)):
            return True
        # The user typed more around the answer ("ozymandias by shelley").
        if (all(t in guess_tokens for t in form_tokens)
                and any(len(t) >= 3 and t not in _ANSWER_STOPWORDS
                        for t in form_tokens)):
            return True
    return False
