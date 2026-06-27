import json
import hashlib
import re
from difflib import SequenceMatcher
import numpy as np

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

MODEL_NAME = "all-MiniLM-L6-v2"
_model = None

STOP_WORDS = {
    "a", "an", "and", "are", "as", "for", "i", "im", "in", "is", "it", "like",
    "me", "movie", "of", "on", "or", "something", "that", "the", "to", "want",
    "watch", "with", "movies", "film", "films", "recommend", "give", "show",
}

SEARCH_INTENTS = {
    "cry": {
        "label": "cry likelihood",
        "triggers": {"cry", "cried", "crying", "sob", "weep", "tears", "tearjerker", "heartbreaking", "devastating", "emotional"},
        "genres": {"Drama", "Romance", "War"},
        "terms": {
            "death", "dying", "grief", "loss", "love", "mother", "orphaned",
            "patient", "touching", "war", "family", "hospital", "farewell",
            "illness", "alone", "friendship", "sacrifice", "tragedy",
            "deported", "execution", "condemned", "deaf", "atone",
        },
        "title_boosts": {
            "Grave of the Fireflies": 32,
            "The Green Mile": 30,
            "Five Feet Apart": 28,
            "Life Is Beautiful": 28,
            "A Silent Voice: The Movie": 25,
            "The Art of Racing in the Rain": 24,
            "Coco": 20,
            "The Pianist": 18,
            "The Boy, the Mole, the Fox and the Horse": 18,
            "Dead Poets Society": 16,
        },
    },
    "cheer": {
        "label": "cheer-up likelihood",
        "triggers": {"sad", "down", "depressed", "upset", "bad", "cheer", "happy", "comfort", "uplift", "uplifting", "feelgood", "feel-good"},
        "genres": {"Comedy", "Family", "Animation", "Adventure", "Music"},
        "terms": {
            "friend", "friends", "family", "music", "adventure", "dream", "fun",
            "happy", "heart", "magical", "whimsical", "hope", "love", "journey",
            "wonder", "kid", "children", "sing", "dance",
        },
        "title_boosts": {
            "Paddington": 24,
            "Shrek": 18,
            "Coco": 18,
            "Puss in Boots: The Last Wish": 18,
            "Monsters, Inc.": 18,
            "Toy Story": 18,
            "Inside Out": 16,
            "Inside Out 2": 16,
            "Klaus": 16,
            "The Super Mario Bros. Movie": 14,
        },
        "penalty_terms": {"death", "dying", "murder", "war", "deported", "illness", "execution"},
    },
    "funny": {
        "label": "funny match",
        "triggers": {"funny", "comedy", "comedies", "laugh", "laughs", "hilarious", "goofy", "silly", "raunchy"},
        "genres": {"Comedy"},
        "terms": {
            "party", "high", "school", "friends", "friend", "bachelor", "raucous",
            "wisecracking", "goofy", "awkward", "teenage", "fun", "prank",
            "seniors", "college", "undercover",
        },
        "title_boosts": {
            "Superbad": 35,
            "Booksmart": 34,
            "The Hangover": 34,
            "21 Jump Street": 32,
            "American Pie": 30,
            "Project X": 28,
            "Pineapple Express": 26,
            "This Is the End": 24,
            "Step Brothers": 24,
            "Mean Girls": 22,
            "Shrek": 18,
        },
        "penalty_genres": {"Drama", "War", "Horror"},
        "penalty_terms": {"death", "dying", "murder", "grief", "war", "deported", "illness", "execution"},
    },
    "romantic": {
        "label": "romantic match",
        "triggers": {"romantic", "romance", "love", "date", "relationship"},
        "genres": {"Romance", "Drama"},
        "terms": {"love", "couple", "relationship", "heart", "passion", "married", "romance"},
        "title_boosts": {
            "The Notebook": 34,
            "Titanic": 30,
            "Call Me by Your Name": 28,
            "Eternal Sunshine of the Spotless Mind": 26,
            "Five Feet Apart": 24,
        },
    },
    "space": {
        "label": "space sci-fi match",
        "triggers": {"space", "astronaut", "astronauts", "cosmic", "planet", "planets", "galaxy", "galactic"},
        "genres": {"Science Fiction", "Adventure", "Drama"},
        "terms": {
            "space", "astronaut", "astronauts", "planet", "mars", "moon", "nasa",
            "mission", "spaceship", "spacecraft", "orbit", "galaxy", "cosmic",
            "alien", "wormhole", "interstellar", "survival",
        },
        "title_boosts": {
            "Interstellar": 38,
            "The Martian": 36,
            "Project Hail Mary": 34,
            "Gravity": 32,
            "2001: A Space Odyssey": 30,
            "Ad Astra": 28,
            "Contact": 28,
            "Spaceman": 26,
            "Arrival": 24,
            "First Man": 22,
            "Alien": 18,
            "WALL·E": 16,
        },
        "penalty_genres": {"Comedy", "Music"},
        "penalty_terms": {"office", "workplace", "parody"},
    },
    "mind_bending": {
        "label": "mind-bending match",
        "triggers": {"mind", "bending", "mindbending", "mind-bending", "twist", "twisty", "psychological", "weird"},
        "genres": {"Science Fiction", "Mystery", "Thriller"},
        "terms": set(),
        "title_boosts": {
            "Inception": 34,
            "The Matrix": 32,
            "The Prestige": 30,
            "Memento": 30,
            "Shutter Island": 28,
            "Eternal Sunshine of the Spotless Mind": 24,
            "Blade Runner 2049": 22,
        },
    },
    "adventure": {
        "label": "epic adventure match",
        "triggers": {"epic", "adventure", "quest", "journey", "grand", "fantasy"},
        "genres": {"Adventure", "Fantasy", "Action", "Science Fiction"},
        "terms": {
            "quest", "journey", "kingdom", "world", "mission", "battle", "war",
            "fellowship", "empire", "rebellion", "hero", "legend", "mythic",
            "survival", "explore", "universe",
        },
        "title_boosts": {
            "The Lord of the Rings: The Fellowship of the Ring": 40,
            "The Lord of the Rings: The Two Towers": 39,
            "The Lord of the Rings: The Return of the King": 38,
            "The Empire Strikes Back": 35,
            "Star Wars": 33,
            "Dune": 31,
            "Dune: Part Two": 30,
            "Mad Max: Fury Road": 28,
            "Gladiator": 26,
            "Avatar": 24,
            "Pirates of the Caribbean: The Curse of the Black Pearl": 22,
        },
        "penalty_genres": {"Comedy"},
    },
    "nostalgic": {
        "label": "nostalgic match",
        "triggers": {"nostalgic", "nostalgia", "childhood", "cozy", "warm", "remember", "old-school", "oldschool"},
        "genres": {"Family", "Animation", "Comedy", "Adventure", "Drama"},
        "terms": {
            "childhood", "kids", "school", "summer", "friends", "friendship",
            "home", "family", "memory", "memories", "coming-of-age", "coming",
            "warm", "wonder", "toy", "adventure",
        },
        "title_boosts": {
            "Toy Story": 36,
            "The Goonies": 34,
            "Stand by Me": 32,
            "The Sandlot": 30,
            "Ferris Bueller's Day Off": 28,
            "The Breakfast Club": 26,
            "Back to the Future": 25,
            "E.T. the Extra-Terrestrial": 24,
            "Jumanji": 22,
            "Home Alone": 20,
            "The Princess Bride": 20,
        },
        "penalty_genres": {"Horror"},
    },
    "dark_atmospheric": {
        "label": "dark atmospheric match",
        "triggers": {"dark", "atmospheric", "moody", "brooding", "noir", "bleak", "haunting"},
        "genres": {"Thriller", "Mystery", "Crime", "Drama", "Horror", "Science Fiction"},
        "terms": {
            "dark", "mysterious", "mystery", "night", "rain", "crime", "killer",
            "murder", "obsession", "haunting", "bleak", "noir", "detective",
            "corruption", "secret", "dread",
        },
        "title_boosts": {
            "Se7en": 38,
            "Prisoners": 36,
            "Zodiac": 34,
            "Blade Runner 2049": 32,
            "The Batman": 31,
            "No Country for Old Men": 30,
            "Nightcrawler": 29,
            "Shutter Island": 28,
            "Memento": 26,
            "The Girl with the Dragon Tattoo": 24,
            "Gone Girl": 22,
        },
    },
    "thriller": {
        "label": "match likelihood",
        "triggers": {"thriller", "dark", "tense", "suspense", "scary", "atmospheric", "mind-bending", "mindbending"},
        "genres": {"Thriller", "Mystery", "Horror", "Crime"},
        "terms": {"mystery", "killer", "murder", "crime", "dark", "death", "secret"},
        "title_boosts": {
            "Shutter Island": 20,
            "Se7en": 18,
            "Memento": 16,
            "The Silence of the Lambs": 16,
        },
    },
}

GENRE_FEATURES = {
    "Action": {"action", "fight", "hero", "revenge", "mission"},
    "Adventure": {"adventure", "journey", "quest", "explore", "epic"},
    "Animation": {"animated", "imaginative", "family", "whimsical"},
    "Comedy": {"funny", "light", "laugh", "comfort", "feelgood"},
    "Crime": {"crime", "detective", "criminal", "murder", "justice"},
    "Drama": {"emotional", "human", "family", "loss", "relationship"},
    "Family": {"family", "warm", "kids", "comfort", "heart"},
    "Fantasy": {"magic", "myth", "supernatural", "wonder"},
    "Horror": {"scary", "fear", "terror", "haunting"},
    "Music": {"music", "song", "performance", "artist"},
    "Mystery": {"mystery", "secret", "puzzle", "twist"},
    "Romance": {"romance", "love", "relationship", "heartbreak"},
    "Science Fiction": {"scifi", "space", "future", "mindbending", "science"},
    "Thriller": {"tense", "suspense", "paranoia", "danger"},
    "War": {"war", "survival", "sacrifice", "trauma"},
}

CURATED_SIMILARITY_BOOSTS = {
    "Inception": {
        "The Prestige": 52,
        "The Matrix": 50,
        "Memento": 48,
        "Interstellar": 46,
        "Shutter Island": 44,
        "Eternal Sunshine of the Spotless Mind": 38,
        "Blade Runner 2049": 34,
        "Dune": 18,
        "Dune: Part Two": 18,
    },
    "Fight Club": {
        "American History X": 48,
        "Taxi Driver": 46,
        "A Clockwork Orange": 44,
        "Joker": 42,
        "The Departed": 40,
        "Se7en": 38,
        "Memento": 34,
        "Shutter Island": 32,
        "GoodFellas": 28,
        "Scarface": 24,
    },
    "Interstellar": {
        "The Martian": 83,
        "Project Hail Mary": 76,
        "Spaceman": 71,
        "Contact": 68,
        "Ad Astra": 67,
        "Arrival": 61,
        "Gravity": 65,
        "2001: A Space Odyssey": 64,
        "First Man": 62,
        "Dune": 60,
        "Dune: Part Two": 58,
        "Blade Runner 2049": 52,
        "The Matrix": 42,
        "Inception": 40,
        "The Prestige": 34,
    },
    "The Prestige": {
        "Inception": 52,
        "Memento": 44,
        "Shutter Island": 42,
        "The Illusionist": 40,
        "Se7en": 34,
        "The Matrix": 28,
        "Eternal Sunshine of the Spotless Mind": 26,
    },
    "Shutter Island": {
        "Memento": 48,
        "Se7en": 46,
        "The Silence of the Lambs": 44,
        "The Prestige": 38,
        "A Clockwork Orange": 30,
        "Psycho": 28,
        "Fight Club": 26,
    },
    "Superbad": {
        "Booksmart": 54,
        "The Hangover": 52,
        "21 Jump Street": 50,
        "American Pie": 48,
        "Project X": 44,
        "Pineapple Express": 42,
        "This Is the End": 40,
        "Step Brothers": 36,
        "Mean Girls": 32,
        "Shrek": 16,
    },
    "The Notebook": {
        "Titanic": 48,
        "Five Feet Apart": 44,
        "A Silent Voice: The Movie": 36,
        "The Art of Racing in the Rain": 34,
        "Life in a Year": 32,
        "Eternal Sunshine of the Spotless Mind": 30,
        "Call Me by Your Name": 28,
    },
    "Titanic": {
        "The Notebook": 48,
        "Life Is Beautiful": 40,
        "Five Feet Apart": 36,
        "The Art of Racing in the Rain": 30,
        "Call Me by Your Name": 28,
    },
    "Boyhood": {
        "Good Will Hunting": 40,
        "Dead Poets Society": 38,
        "The Truman Show": 32,
        "Perfect Days": 30,
        "Inside Out": 26,
    },
    "The Departed": {
        "GoodFellas": 48,
        "Scarface": 42,
        "Reservoir Dogs": 40,
        "The Godfather": 38,
        "The Godfather Part II": 36,
        "Se7en": 30,
        "Fight Club": 24,
    },
    "The Bourne Identity": {
        "Casino Royale": 44,
        "Mission: Impossible": 40,
        "The Matrix": 32,
        "Top Gun: Maverick": 28,
        "The Departed": 24,
    },
    "Batman Begins": {
        "The Dark Knight": 52,
        "Joker": 44,
        "The Batman": 40,
        "Superman": 28,
        "The Departed": 24,
    },
}

CEREBRAL_TERMS = {
    "subconscious", "dream", "dreams", "memory", "memories", "idea", "mind",
    "mysterious", "mystery", "secret", "vision", "visions", "hacker",
    "impossible", "identity", "obsession", "deceit", "future", "time",
}

SEARCH_INTENTS["mind_bending"]["terms"] = CEREBRAL_TERMS

INTENT_SEED_TITLES = {
    "funny": ["Superbad", "Booksmart", "The Hangover", "21 Jump Street", "American Pie", "Project X", "Pineapple Express", "This Is the End", "Step Brothers", "Mean Girls"],
    "romantic": ["The Notebook", "Titanic", "Call Me by Your Name", "Eternal Sunshine of the Spotless Mind", "Five Feet Apart"],
    "mind_bending": ["Inception", "The Matrix", "The Prestige", "Memento", "Shutter Island", "Blade Runner 2049"],
    "cry": ["Grave of the Fireflies", "Five Feet Apart", "Life Is Beautiful", "A Silent Voice: The Movie", "The Green Mile"],
    "cheer": ["Paddington", "Coco", "Puss in Boots: The Last Wish", "Toy Story", "Inside Out", "Shrek", "The Super Mario Bros. Movie", "School of Rock", "Chef"],
    "space": ["Interstellar", "The Martian", "Project Hail Mary", "Gravity", "2001: A Space Odyssey", "Ad Astra", "Contact", "Spaceman", "Arrival", "First Man", "Alien", "WALL·E"],
    "adventure": ["The Lord of the Rings: The Fellowship of the Ring", "The Lord of the Rings: The Two Towers", "The Lord of the Rings: The Return of the King", "The Empire Strikes Back", "Star Wars", "Dune", "Dune: Part Two", "Mad Max: Fury Road", "Gladiator", "Avatar"],
    "nostalgic": ["Toy Story", "The Goonies", "Stand by Me", "The Sandlot", "Ferris Bueller's Day Off", "The Breakfast Club", "Back to the Future", "E.T. the Extra-Terrestrial", "Jumanji", "Home Alone"],
    "dark_atmospheric": ["Se7en", "Prisoners", "Zodiac", "Blade Runner 2049", "The Batman", "No Country for Old Men", "Nightcrawler", "Shutter Island", "Memento", "Gone Girl"],
}

TAG_RULES = {
    "emotion": {
        "emotional": {"emotional", "heart", "touching", "love", "family", "father", "daughter", "mother"},
        "hopeful": {"hope", "survive", "journey", "dream", "restore", "future"},
        "melancholic": {"loss", "grief", "alone", "memory", "farewell", "heartbroken"},
        "mind-bending": CEREBRAL_TERMS | {"twist", "paradox", "subconscious"},
        "devastating": SEARCH_INTENTS["cry"]["terms"] | {"concentration", "air", "raid"},
        "comforting": SEARCH_INTENTS["cheer"]["terms"] | {"warm", "joy", "home"},
        "funny": {"funny", "comedy", "party", "laugh", "hilarious"},
        "dark": {"murder", "crime", "killer", "revenge", "corruption", "underworld"},
        "romantic": {"love", "romance", "couple", "relationship", "passion"},
    },
    "vibe": {
        "atmospheric": {"atmospheric", "mysterious", "vision", "secret", "night", "rain", "neon"},
        "epic": {"epic", "universe", "galaxy", "war", "journey", "quest"},
        "intimate": {"family", "relationship", "couple", "parents", "childhood"},
        "tense": {"thriller", "danger", "murder", "crime", "fighting", "dead"},
        "nostalgic": {"childhood", "memory", "memories", "old", "years"},
    },
    "tone": {
        "serious": {"drama", "crime", "war", "death", "loss"},
        "philosophical": {"existence", "humanity", "future", "meaning", "identity", "mind"},
        "fast-paced": {"action", "mission", "spy", "fight", "chase"},
        "slow-burn": {"mysterious", "investigates", "obsession", "memory"},
        "light": {"comedy", "family", "fun", "happy", "adventure"},
    },
}

def tokenize(text):
    if not text:
        return set()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return {w for w in text.split() if w and w not in STOP_WORDS}

def clean_title(title):
    if not title:
        return ""
    return re.sub(r"[^\w\s]", "", title.lower()).strip()

def split_genres(movie_orm):
    if not movie_orm.genres:
        return set()
    return {g.strip() for g in movie_orm.genres.split(",") if g.strip()}

def clamp_score(score):
    return max(0.0, min(100.0, round(score, 1)))

def movie_text(movie_orm):
    parts = [movie_orm.title or "", movie_orm.overview or "", movie_orm.genres or ""]
    return " ".join(parts).lower()

def infer_tags(movie_orm):
    text = movie_text(movie_orm)
    movie_genres = {g.lower() for g in split_genres(movie_orm)}
    assigned = {"emotion": [], "vibe": [], "tone": []}
    for category, tags_dict in TAG_RULES.items():
        for tag, keywords in tags_dict.items():
            match_count = sum(1 for kw in keywords if kw in text or kw in movie_genres)
            if match_count >= 2 or (tag in ["funny", "romantic"] and tag in movie_genres):
                assigned[category].append(tag)
    return assigned

def semantic_text(movie_data):
    title = movie_data.get("title", "")
    overview = movie_data.get("overview", "")
    genres = ", ".join(movie_data.get("genres", []))
    return f"Title: {title}. Overview: {overview}. Genres: {genres}."

def weighted_features(movie_orm):
    tokens = tokenize(movie_text(movie_orm))
    genres = split_genres(movie_orm)
    features = {}
    for token in tokens:
        features[token] = features.get(token, 0.0) + 1.0
    for genre in genres:
        features[f"genre_{genre.lower()}"] = 4.0
        if genre in GENRE_FEATURES:
            for core_word in GENRE_FEATURES[genre]:
                features[core_word] = features.get(core_word, 0.0) + 1.5
    return features

def top_tags(movie_orm, limit=3):
    tags = infer_tags(movie_orm)
    all_tags = tags["emotion"] + tags["vibe"] + tags["tone"]
    return all_tags[:limit] if all_tags else ["movie"]

def reason_from_tags(prefix, movie_orm, fallback):
    tags = top_tags(movie_orm, 2)
    if tags:
        return f"{prefix} its {' & '.join(tags)} mood features."
    return fallback

def feature_cosine(f1, f2):
    all_keys = set(f1.keys()) | set(f2.keys())
    if not all_keys:
        return 0.0
    v1 = np.array([f1.get(k, 0.0) for k in all_keys])
    v2 = np.array([f2.get(k, 0.0) for k in all_keys])
    n1 = np.linalg.norm(v1)
    n2 = np.linalg.norm(v2)
    if n1 == 0 or n2 == 0:
        return 0.0
    return float(np.dot(v1, v2) / (n1 * n2))

def find_reference_movie(query_text, candidate_movies):
    cleaned_q = clean_title(query_text)
    if not cleaned_q or len(cleaned_q) < 3:
        return None
    best_movie = None
    best_score = 0.0
    for movie in candidate_movies:
        cleaned_t = clean_title(movie.title)
        if cleaned_q == cleaned_t:
            return movie
        ratio = SequenceMatcher(None, cleaned_q, cleaned_t).ratio()
        if ratio > best_score:
            best_score = ratio
            best_movie = movie
    if best_score >= 0.85:
        return best_movie
    return None

def infer_search_intent(query_text, candidate_movies):
    tokens = tokenize(query_text)
    trigger_tokens = set()
    for config in SEARCH_INTENTS.values():
        trigger_tokens |= config["triggers"]
    mood_only_query = bool(tokens) and not (tokens - trigger_tokens)

    ref = find_reference_movie(query_text, candidate_movies)
    if ref and not mood_only_query:
        return {"type": "similar", "label": "similarity likelihood", "reference": ref}
    for intent, config in SEARCH_INTENTS.items():
        if config["triggers"] & tokens:
            return {"type": intent, "label": config["label"], "reference": None}
    return {"type": "general", "label": "match confidence", "reference": None}

def is_transformer_available():
    return SentenceTransformer is not None

def get_model():
    global _model
    if not is_transformer_available():
        return None
    if _model is None:
        try:
            _model = SentenceTransformer(MODEL_NAME)
        except Exception:
            return None
    return _model

def embed_text(text):
    model = get_model()
    if not model:
        return []
    emb = model.encode(text, convert_to_numpy=True)
    return emb.tolist()

def embed_movie(movie_data):
    return embed_text(semantic_text(movie_data))

def embedding_to_json(embedding_list):
    return json.dumps(embedding_list)

def json_to_embedding(json_str):
    if not json_str:
        return np.array([])
    try:
        # Handle cases where it might already be a list or array
        if isinstance(json_str, (list, np.ndarray)):
            return np.array(json_str)
        # Standard case: JSON string
        data = json.loads(json_str)
        return np.array(data)
    except (json.JSONDecodeError, TypeError, ValueError):
        # Graceful fallback for malformed data
        return np.array([])

def cosine_score(v1, v2):
    if len(v1) == 0 or len(v2) == 0:
        return 0.0
    n1 = np.linalg.norm(v1)
    n2 = np.linalg.norm(v2)
    if n1 == 0 or n2 == 0:
        return 0.0
    return float(np.dot(v1, v2) / (n1 * n2))

def score_mood_movie(intent_type, movie_orm):
    config = SEARCH_INTENTS.get(intent_type)
    if not config:
        return 50.0
    text = movie_text(movie_orm)
    movie_genres = split_genres(movie_orm)
    score = 30.0
    genre_match = len(movie_genres & config["genres"])
    score += genre_match * 12.0
    term_match = sum(2.5 for term in config["terms"] if term in text)
    score += term_match
    boosts = config.get("title_boosts", {})
    if movie_orm.title in boosts:
        score += boosts[movie_orm.title]
    if config.get("penalty_genres") and (movie_genres & config["penalty_genres"]):
        score -= 10.0  # Reduced from 25.0
    if config.get("penalty_terms"):
        penalty_match = sum(2.0 for term in config["penalty_terms"] if term in text) # Reduced from 4.0
        score -= penalty_match
    score += min(movie_orm.vote_average or 0, 10) * 0.8
    return clamp_score(score)

def mood_reason(intent_type, movie_orm):
    config = SEARCH_INTENTS.get(intent_type)
    if not config:
        return reason_from_tags("Matches criteria indexing", movie_orm, "Recommended based on contextual keyword alignment.")
        
    tags = top_tags(movie_orm, 2)
    label = config["label"].replace(" likelihood", "").replace(" match", "")
    if tags:
        return f"Matches the {label} parameters highlighting structural {' & '.join(tags)} themes."
    return f"High analytical indexing score against our core {label} mood definitions matrix."

def score_similar_movies(reference_movie, candidate_movies):
    """
    Finds movies similar to a reference anchor.
    Prioritizes:
    1. Neural Embedding similarity (if available)
    2. Genre overlap
    3. Curated boosts for iconic pairings
    """
    ref_title = reference_movie.title or ""
    curated_boosts = CURATED_SIMILARITY_BOOSTS.get(ref_title, {})
    
    ref_embedding = json_to_embedding(reference_movie.embedding)
    ref_genres = split_genres(reference_movie)
    
    results = []
    for movie in candidate_movies:
        if movie.tmdb_id == reference_movie.tmdb_id:
            continue
            
        # 1. Semantic Similarity (Max 60)
        semantic_score = 0.0
        movie_embedding = json_to_embedding(movie.embedding)
        if len(ref_embedding) > 0 and len(movie_embedding) > 0:
            sim = cosine_score(ref_embedding, movie_embedding)
            # Use a steeper curve for similarity: max(0, sim)^2
            semantic_score = max(0, sim) * 60.0
        else:
            # Fallback to lexical feature cosine if no embeddings
            f1 = weighted_features(reference_movie)
            f2 = weighted_features(movie)
            sim = feature_cosine(f1, f2)
            semantic_score = sim * 40.0
            
        # 2. Genre Overlap (Max 20)
        movie_genres = split_genres(movie)
        overlap = len(ref_genres & movie_genres)
        genre_score = min(overlap * 7.0, 20.0)
        
        # 3. Curated & Meta Boosts (Max 20)
        meta_score = 0.0
        curated_boost = curated_boosts.get(movie.title or "", 0)
        # Normalize curated boost to a 0-20 metadata component.
        meta_score += (curated_boost / 54.0) * 20.0
        
        # Quality boost
        meta_score += min(movie.vote_average or 0, 10) * 0.5
        meta_score = min(meta_score, 20.0)
        
        total_score = semantic_score + genre_score + meta_score
        if curated_boost:
            total_score = max(total_score, curated_boost)
        
        reason = reason_from_tags(
            f"Similar to {ref_title} through",
            movie,
            f"Shares thematic DNA and tone with {ref_title}."
        )
        
        results.append({
            "movie": movie, 
            "score": clamp_score(total_score), 
            "semantic": round(semantic_score, 1),
            "genre": round(genre_score, 1),
            "meta": round(meta_score, 1),
            "reason": reason
        })
        
    results.sort(key=lambda item: item["score"], reverse=True)
    
    # Debug logging
    print(f"\n[SIMILARITY DEBUG] Reference: '{ref_title}'")
    for i, res in enumerate(results[:3]):
        m = res['movie']
        print(f"  #{i+1} {m.title[:30]:<30} | Total: {res['score']:>5} | S: {res['semantic']:>4} | G: {res['genre']:>4} | M: {res['meta']:>4}")
    print("-" * 80)
    
    return results

def score_general_text(query_text, movie_orm):
    query_tokens = tokenize(query_text)
    if not query_tokens:
        return 30.0
    text = movie_text(movie_orm)
    matched = sum(6.0 for tok in query_tokens if tok in text)
    title_matched = sum(14.0 for tok in query_tokens if tok in (movie_orm.title or "").lower())
    score = 25.0 + matched + title_matched + (min(movie_orm.vote_average or 0, 10) * 0.9)
    return clamp_score(score)

def score_movies_by_text(query_text, candidate_movies):
    intent = infer_search_intent(query_text, candidate_movies)
    results = []
    if intent["type"] == "similar":
        return score_similar_movies(intent["reference"], candidate_movies), intent
    for movie in candidate_movies:
        if intent["type"] == "general":
            score = score_general_text(query_text, movie)
            reason = reason_from_tags("Matches criteria indexing", movie, "Matches text pattern keywords query indicators.")
        else:
            score = score_mood_movie(intent["type"], movie)
            reason = mood_reason(intent["type"], movie)
        results.append({"movie": movie, "score": score, "reason": reason})
    results.sort(key=lambda item: item["score"], reverse=True)
    return results, intent

def build_taste_profile(logged_movies):
    if not logged_movies:
        return None
    vectors = []
    weights = []
    for m in logged_movies:
        try:
            vec = json_to_embedding(m.embedding)
            if len(vec) > 0:
                user_rating = m.rating or 7.0
                weight = 1.0 + (user_rating - 6.0) * 0.25 if user_rating >= 6.0 else 0.4
                vectors.append(vec / np.linalg.norm(vec) if np.linalg.norm(vec) > 0 else vec)
                weights.append(weight)
        except Exception:
            continue
    if not vectors:
        return None
    taste = np.average(vectors, axis=0, weights=weights)
    return taste / np.linalg.norm(taste) if np.linalg.norm(taste) > 0 else taste

def build_taste_profile_text(logged_movies):
    features = {}
    for m in logged_movies:
        user_rating = m.rating or 7.0
        weight = 1.0 + (user_rating - 5.0) * 0.3
        m_feats = weighted_features(m)
        for k, v in m_feats.items():
            features[k] = features.get(k, 0.0) + (v * weight)
    return features

def score_movies_by_taste_text(taste_features, candidate_movies):
    results = []
    for movie in candidate_movies:
        movie_feats = weighted_features(movie)
        sim = feature_cosine(taste_features, movie_feats)
        score = 25.0 + (sim * 60.0) + (min(movie.vote_average or 0, 10) * 0.6)
        reason = reason_from_tags("Aligns with your taste profile in", movie, "Recommended based on overall logged watch preferences history.")
        results.append({"movie": movie, "score": clamp_score(score), "reason": reason})
    results.sort(key=lambda item: item["score"], reverse=True)
    return results

def profile_summary(logged_movies):
    if not logged_movies:
        return "Your profile is empty. Log and rate movies to initialize your neural engine summary map."
    genres_count = {}
    tokens_count = {}
    for m in logged_movies:
        weight = max(0.5, (m.rating or 6.0) / 6.0)
        for g in split_genres(m):
            genres_count[g] = genres_count.get(g, 0.0) + weight
        for tok in tokenize(m.overview):
            tokens_count[tok] = tokens_count.get(tok, 0.0) + weight
    top_g = sorted(genres_count.items(), key=lambda x: x[1], reverse=True)[:2]
    top_t = sorted(tokens_count.items(), key=lambda x: x[1], reverse=True)[:3]
    genre_str = " & ".join([g[0] for g in top_g]) if top_g else "Diverse themes"
    keywords_str = ", ".join([t[0] for t in top_t]) if top_t else "various cinematic beats"
    return f"Neural alignment strongly favors {genre_str} cinematic spaces, heavily indexing on motifs related to: {keywords_str}."

def score_movies_by_query(query_embedding, candidate_movies, taste_profile=None, query_weight=0.75, taste_weight=0.25):
    results = []
    query_vec = query_embedding / np.linalg.norm(query_embedding) if np.linalg.norm(query_embedding) > 0 else query_embedding
    for movie in candidate_movies:
        try:
            movie_embedding = json_to_embedding(movie.embedding)
            movie_vec = movie_embedding / np.linalg.norm(movie_embedding) if np.linalg.norm(movie_embedding) > 0 else movie_embedding
            query_sim = cosine_score(query_vec, movie_vec)
            query_sim_norm = (query_sim + 1.0) / 2.0
            if taste_profile is not None:
                taste_sim = cosine_score(taste_profile, movie_vec)
                taste_sim_norm = (taste_sim + 1.0) / 2.0
                score = query_weight * query_sim_norm + taste_weight * taste_sim_norm
            else:
                score = query_sim_norm
            percentage = round(score * 100, 1)
            results.append({"movie": movie, "score": percentage})
        except Exception:
            continue
    results.sort(key=lambda x: x["score"], reverse=True)
    return results

def score_movies_by_taste(taste_profile, candidate_movies):
    results = []
    for movie in candidate_movies:
        try:
            movie_embedding = json_to_embedding(movie.embedding)
            movie_vec = movie_embedding / np.linalg.norm(movie_embedding) if np.linalg.norm(movie_embedding) > 0 else movie_embedding
            taste_sim = cosine_score(taste_profile, movie_vec)
            taste_sim_norm = (taste_sim + 1.0) / 2.0
            percentage = round(taste_sim_norm * 100, 1)
            results.append({"movie": movie, "score": percentage})
        except Exception:
            continue
    results.sort(key=lambda x: x["score"], reverse=True)
    return results

def execute_two_stage_rank(query_text: str, candidate_movies, reference_movie=None, taste_profile=None):
    """
    A robust hybrid ranking pipeline (40/40/20).
    Combines:
    - Semantic Similarity (40%)
    - Lexical/Phrase matching (40%)
    - Curated Intent/Mood (20%)
    Includes personal taste nudge and detailed debug logging.
    """
    # 1. Handle explicit similarity anchor (e.g. "Similar to Inception")
    if reference_movie is not None:
        return score_similar_movies(reference_movie, candidate_movies)
        
    # 2. Stage 1: Feature Extraction
    query_tokens = tokenize(query_text)
    intent = infer_search_intent(query_text, candidate_movies)
    
    query_embedding = None
    if is_transformer_available():
        emb = embed_text(query_text)
        if emb:
            query_embedding = np.array(emb)
    
    results = []
    
    # 3. Stage 2: Hybrid Scoring Loop
    for movie in candidate_movies:
        # A. Semantic Component (Max 40)
        semantic_score = 0.0
        if query_embedding is not None:
            movie_embedding = json_to_embedding(movie.embedding)
            if len(movie_embedding) > 0:
                sim = cosine_score(query_embedding, movie_embedding)
                # Use max(0, sim) to avoid boosting unrelated movies
                semantic_score = max(0, sim) * 40.0
        
        # B. Lexical Component (Max 40)
        lexical_score = 0.0
        title_clean = clean_title(movie.title)
        query_clean = clean_title(query_text)
        movie_genres = {g.lower() for g in split_genres(movie)}
        
        # Exact title match
        if query_clean == title_clean:
            lexical_score += 40.0 # Max out for exact title
        # Phrase match in title
        elif query_clean and query_clean in title_clean:
            lexical_score += 25.0
        # Partial token match in title
        elif any(t in title_clean for t in query_tokens):
            lexical_score += 15.0
            
        # Genre match
        if any(t in movie_genres for t in query_tokens):
            lexical_score += 15.0
            
        lexical_score = min(lexical_score, 40.0)
        if intent["type"] != "general":
            lexical_score = 0.0
        
        # C. Intent Component (Max 20)
        intent_score = 0.0
        if intent["type"] != "general":
            raw_intent = score_mood_movie(intent["type"], movie)
            intent_score = (raw_intent / 100.0) * 20.0
        else:
            raw_gen = score_general_text(query_text, movie)
            intent_score = (raw_gen / 100.0) * 20.0
            
        # D. Genre Safety Net: If searching for a genre specifically, penalize non-genre movies
        # E.g., if query is "comedy", but movie is not comedy, hit it hard.
        genre_triggers = {"comedy", "horror", "action", "sci-fi", "scifi", "drama", "romance", "thriller", "war", "animation", "family"}
        query_genres = query_tokens & genre_triggers
        if query_genres and not (query_genres & movie_genres):
            # Significant penalty for mismatching an explicit genre query
            intent_score -= 15.0
            
        # Total Hybrid Score
        total_score = semantic_score + lexical_score + intent_score

        if (
            intent["type"] in {"mind_bending", "thriller"}
            and "music" in movie_genres
            and not (query_tokens & {"music", "musical", "concert", "song", "songs"})
        ):
            total_score -= 25.0
        
        # D. Personalization Nudge (Taste Profile)
        if taste_profile is not None and is_transformer_available():
            movie_embedding = json_to_embedding(movie.embedding)
            if len(movie_embedding) > 0:
                taste_sim = cosine_score(taste_profile, movie_embedding)
                taste_sim_norm = ((taste_sim + 1.0) / 2.0) * 100.0
                # Apply 10% nudge
                total_score = (total_score * 0.9) + (taste_sim_norm * 0.1)

        # Reason construction
        if intent["type"] != "general":
            reason = mood_reason(intent["type"], movie)
        else:
            reason = reason_from_tags("Matches search criteria through", movie, "High contextual relevance.")

        results.append({
            "movie": movie,
            "score": clamp_score(total_score),
            "semantic": round(semantic_score, 1),
            "lexical": round(lexical_score, 1),
            "intent": round(intent_score, 1),
            "reason": reason
        })
        
    # Sort results
    results.sort(key=lambda x: x["score"], reverse=True)

    # Normalize to a 0-100 relative scale so the top hit is near 100 and
    # other results are presented as an intuitive percentage of the best match.
    try:
        max_raw = max((r["score"] for r in results), default=0.0)
        if max_raw > 0:
            for r in results:
                # scale proportionally and clamp
                r["score"] = clamp_score((r["score"] / max_raw) * 100.0)
    except Exception:
        # If normalization fails, leave original scores intact
        pass
    
    # 4. Stage 3: Terminal Debug Logging
    print(f"\n[RANKER DEBUG] Query: '{query_text}' | Intent: {intent['type']}")
    for i, res in enumerate(results[:3]):
        m = res['movie']
        print(f"  #{i+1} {m.title[:30]:<30} | Total: {res['score']:>5} | S: {res['semantic']:>4} | L: {res['lexical']:>4} | I: {res['intent']:>4}")
    print("-" * 80)
    
    return results
