"""Curated concept vocabulary for OpenCLIP zero-shot tagging.

These strings are fed to CLIP's text encoder once at startup; each
image's embedding is then compared against all of them at inference
time to produce the top-K concept tags. The point is to surface
high-level signals Florence-2 dense captioning won't naturally
mention — environment subtypes, lighting, mood, season, photo style.

Categories are organized for readability only; CLIP doesn't know which
category a tag came from. Keep entries human-readable and unambiguous;
short noun phrases work better than single adjectives.

Current size: ~450 entries. Expand over time based on what the held-
out eval set surfaces as missing. The runtime tensor is cached after
first encode (~50 ms one-time cost on GPU, ~2 s on CPU), so adding
entries doesn't slow per-image inference.
"""

ENVIRONMENT = [
    # Outdoor — natural
    "mountain landscape", "snowy peaks", "forest", "dense forest", "pine forest",
    "tropical jungle", "desert", "sand dunes", "rocky desert", "canyon",
    "beach", "tropical beach", "rocky coast", "cliffs by the sea", "ocean",
    "lake", "river", "waterfall", "stream", "wetland", "marsh",
    "grassland", "meadow", "field of flowers", "vineyard", "orchard", "farm",
    "rolling hills", "valley", "glacier", "volcano", "cave",
    # Outdoor — built
    "city street", "narrow alley", "busy intersection", "highway", "country road",
    "rural road", "dirt path", "hiking trail",
    "park", "city park", "playground", "garden", "backyard",
    "rooftop", "balcony", "patio", "deck", "pool deck",
    "marina", "harbor", "dock", "pier", "bridge",
    "airport runway", "train platform", "subway station", "bus stop",
    "parking lot", "gas station", "construction site", "scaffolding",
    "stadium", "arena", "amphitheater", "racetrack", "ski slope",
    "amusement park", "fairground", "carnival",
    # Indoor — residential
    "kitchen", "modern kitchen", "open-plan kitchen", "living room",
    "bedroom", "child's bedroom", "nursery", "home office", "study",
    "bathroom", "laundry room", "garage", "basement", "attic",
    "hallway", "entryway", "staircase", "dining room", "breakfast nook",
    "walk-in closet", "pantry",
    # Indoor — commercial / public
    "office", "open-plan office", "conference room", "meeting room",
    "classroom", "lecture hall", "computer lab", "library", "bookstore",
    "cafe", "coffee shop", "restaurant", "fine-dining restaurant",
    "bar", "pub", "nightclub", "diner", "food court",
    "grocery store", "supermarket", "convenience store", "farmers market",
    "shopping mall", "department store", "boutique", "clothing store",
    "museum", "art gallery", "concert hall", "theater", "movie theater",
    "church interior", "temple interior", "mosque interior", "synagogue interior",
    "hospital ward", "doctor's office", "dental office", "veterinary clinic",
    "salon", "barbershop", "spa", "gym", "fitness studio", "yoga studio",
    "laboratory", "factory floor", "warehouse", "workshop", "studio",
    "art studio", "photography studio", "recording studio",
    "hotel room", "hotel lobby", "airport lounge", "waiting room",
]

LIGHTING = [
    "golden hour", "blue hour", "harsh midday light", "overcast sky",
    "stormy sky", "dramatic sunset", "sunrise", "twilight", "dusk", "dawn",
    "low light", "candlelit", "neon lighting", "fluorescent lighting",
    "tungsten lighting", "backlit", "silhouette",
    "soft window light", "harsh shadow", "long shadows",
    "bright sunlight", "diffused light", "studio lighting",
    "dim ambient lighting", "moody dark scene", "high-key bright scene",
]

WEATHER_SEASON = [
    "sunny day", "cloudy day", "overcast day", "rainy", "stormy", "foggy",
    "misty morning", "snowy", "blizzard", "icy",
    "spring", "summer", "autumn", "fall foliage", "winter", "frost",
    "wet pavement", "rain on window", "puddle reflection",
]

ACTIVITY = [
    # People doing things
    "writing on a whiteboard", "presenting slides", "giving a speech",
    "teaching a class", "studying", "reading a book", "taking notes",
    "working at a laptop", "coding", "video call", "phone call",
    "cooking", "baking", "chopping vegetables", "stirring a pot",
    "eating", "having dinner", "drinking coffee", "drinking wine", "toasting glasses",
    "hiking", "climbing", "running", "walking", "jogging",
    "cycling", "skateboarding", "surfing", "swimming", "diving",
    "skiing", "snowboarding", "ice skating", "sledding",
    "yoga", "meditation", "stretching", "lifting weights",
    "playing a guitar", "playing piano", "playing drums", "singing",
    "dancing", "playing chess", "playing cards", "playing video games",
    "painting", "drawing", "sculpting", "pottery",
    "gardening", "planting", "watering plants", "raking leaves",
    "shopping", "browsing shelves", "carrying groceries",
    "playing with a child", "carrying a child", "feeding a baby",
    "petting a dog", "walking a dog", "holding a cat",
    "construction work", "carpentry", "fixing a car", "painting a wall",
    "fishing", "boating", "kayaking", "paddleboarding",
    "selfie", "group photo", "wedding portrait", "graduation portrait",
]

AESTHETIC_STYLE = [
    "minimalist", "candid", "posed", "action shot", "macro photo",
    "wide-angle landscape", "telephoto close-up", "black and white",
    "high contrast", "low contrast", "saturated colors", "muted colors",
    "warm tones", "cool tones", "vintage tone", "film grain",
    "blurred background", "shallow depth of field", "deep focus",
    "long exposure", "motion blur", "tilt-shift",
    "drone aerial", "top-down view", "ground-level shot", "eye-level portrait",
    "rule of thirds composition", "symmetrical composition", "leading lines",
    "framed shot", "abstract", "geometric", "pattern", "texture",
]

CONTENT_TYPE = [
    "screenshot", "phone screenshot", "computer screenshot",
    "document scan", "receipt", "handwritten note", "printed page",
    "whiteboard", "blackboard", "chalkboard",
    "graph", "chart", "diagram", "infographic", "flowchart", "table",
    "code on screen", "terminal output", "spreadsheet",
    "slide deck", "presentation slide",
    "comic panel", "manga page",
    "meme", "logo", "poster", "sign", "banner", "billboard",
    "map", "schematic", "blueprint", "architectural drawing",
    "x-ray", "ultrasound", "medical scan",
]

OBJECTS_NOUNS = [
    # Common nouns Florence-2 OD might miss or label too generically
    "coffee mug", "espresso machine", "kettle", "tea kettle",
    "frying pan", "saucepan", "wooden spoon", "knife block",
    "blue marker", "permanent marker", "highlighter", "fountain pen",
    "notebook", "spiral notebook", "moleskine", "sticky notes",
    "monitor", "external monitor", "mechanical keyboard", "trackpad",
    "headphones", "earbuds", "studio headphones",
    "guitar", "electric guitar", "bass guitar", "violin", "cello",
    "yoga mat", "dumbbells", "kettlebell", "treadmill",
    "soccer ball", "basketball", "baseball bat", "tennis racket",
    "skateboard", "surfboard", "snowboard", "kayak",
    "candle", "lantern", "fireplace", "campfire",
    "houseplant", "succulent", "bouquet of flowers", "potted tree",
    "bookshelf full of books", "pile of laundry", "stack of dishes",
]

PEOPLE_RELATIONSHIPS = [
    "solo portrait", "couple", "family", "group of friends", "team photo",
    "wedding party", "birthday party", "graduation ceremony",
    "concert crowd", "sports crowd", "protest crowd",
]

VOCAB: list[str] = (
    ENVIRONMENT
    + LIGHTING
    + WEATHER_SEASON
    + ACTIVITY
    + AESTHETIC_STYLE
    + CONTENT_TYPE
    + OBJECTS_NOUNS
    + PEOPLE_RELATIONSHIPS
)
