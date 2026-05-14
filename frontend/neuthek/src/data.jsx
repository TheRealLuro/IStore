// Mock data for the prototype.
// Photos use Unsplash placeholders; faces use UI-Faces-style randomuser.me.
//
// Backbone-wired flows (auth, file listing, upload, storage, sign-out) hit the
// real backend via src/api/*.ts. Everything else (faces, contacts, passwords,
// game saves, IoT events, recent searches, sample folders) is still mock — see
// todo.md section E for the backend roadmap that lights these up.
export const PHOTOS = [
  "https://images.unsplash.com/photo-1469474968028-56623f02e42e?w=800",
  "https://images.unsplash.com/photo-1501785888041-af3ef285b470?w=800",
  "https://images.unsplash.com/photo-1418065460487-3e41a6c84dc5?w=800",
  "https://images.unsplash.com/photo-1426604966848-d7adac402bff?w=800",
  "https://images.unsplash.com/photo-1447752875215-b2761acb3c5d?w=800",
  "https://images.unsplash.com/photo-1493246507139-91e8fad9978e?w=800",
  "https://images.unsplash.com/photo-1470770841072-f978cf4d019e?w=800",
  "https://images.unsplash.com/photo-1505765050516-f72dcac9c60b?w=800",
  "https://images.unsplash.com/photo-1500964757637-c85e8a162699?w=800",
  "https://images.unsplash.com/photo-1485470733090-0aae1788d5af?w=800",
  "https://images.unsplash.com/photo-1465056836041-7f43ac27dcb5?w=800",
  "https://images.unsplash.com/photo-1444080748397-f442aa95c3e5?w=800",
];

export const FACES = [
  { id: "f1", name: "Sasha Chen", img: "https://i.pravatar.cc/120?img=47", count: 24 },
  { id: "f2", name: "Marcus Reid", img: "https://i.pravatar.cc/120?img=12", count: 18 },
  { id: "f3", name: null, img: "https://i.pravatar.cc/120?img=33", count: 9 },
  { id: "f4", name: "Léa Bonneau", img: "https://i.pravatar.cc/120?img=49", count: 7 },
  { id: "f5", name: null, img: "https://i.pravatar.cc/120?img=15", count: 5 },
  { id: "f6", name: "Tomás Vega", img: "https://i.pravatar.cc/120?img=68", count: 4 },
  { id: "f7", name: null, img: "https://i.pravatar.cc/120?img=20", count: 3 },
];

// Status tags users can apply to files.
export const TAGS = [
  { id: "fav",      label: "Favorite",   tone: "ink" },
  { id: "review",   label: "To review",  tone: "warn" },
  { id: "shared",   label: "Shared",     tone: "neutral" },
  { id: "archived", label: "Archived",   tone: "muted" },
  { id: "private",  label: "Private",    tone: "ink" },
  { id: "wip",      label: "WIP",        tone: "warn" },
];

// Files now span 7 categories. Each carries a `category` so the chip filter
// works, plus richer AI content the new semantic search can hit.
export const FILES = [
  // images / videos / docs
  { id: "1",  type: "image", category: "image",     name: "Coastal-trip-day-2.jpg", size: "2.4 MB", when: "2 days ago", thumb: PHOTOS[0],  ext: "JPG",
    topic: "Sunset over the cliffs at Big Sur",
    aiContent: "Wide-angle landscape, golden hour, coastal cliffs, Pacific Ocean horizon, low sun, warm orange and pink sky. Two people standing on the rock outcrop on the right.",
    gps: { lat: 36.27, lng: -121.81, place: "Big Sur, CA" },
    tags: ["fav"], folder: "fo1" },
  { id: "2",  type: "image", category: "image",     name: "Studio-portrait-final.png", size: "4.1 MB", when: "3 days ago", thumb: PHOTOS[1], ext: "PNG",
    topic: "Studio portrait, soft daylight, blue backdrop",
    aiContent: "Close-up portrait of a woman with brown hair, warm smile, looking past camera. Soft window light from left. Blue paper backdrop.",
    gps: { lat: 37.78, lng: -122.42, place: "San Francisco, CA" },
    tags: ["review"], folder: "fo2" },
  { id: "3",  type: "video", category: "video",     name: "Hiking-clip-001.mp4", size: "32 MB", when: "5 days ago", thumb: PHOTOS[2], ext: "MP4",
    topic: "Forest hike, handheld, light foot traffic",
    aiContent: "00:42 handheld walking shot through redwood forest. Dappled sunlight. Wooden boardwalk. Distant voices.",
    gps: { lat: 37.88, lng: -122.10, place: "Berkeley Hills, CA" },
    tags: [], folder: "fo1" },
  { id: "4",  type: "image", category: "image",     name: "Architecture-002.jpg", size: "1.8 MB", when: "1 week ago", thumb: PHOTOS[3], ext: "JPG",
    topic: "Modernist concrete facade, hard shadow",
    aiContent: "Brutalist concrete building. Strong diagonal shadow across the facade. Single window, low-angle composition.",
    gps: { lat: 34.05, lng: -118.24, place: "Los Angeles, CA" },
    tags: [], folder: "fo2" },
  { id: "5",  type: "doc",   category: "document",  name: "Q1-Plan.pdf", size: "120 KB", when: "1 week ago", thumb: null, ext: "PDF",
    topic: "Strategy doc — Q1 priorities and headcount",
    aiContent: "12 pages. Sections: 'Goals for Q1', 'Hiring plan (3 roles)', 'Risk register', 'Budget rollover'. Mentions: Atlas launch, mobile rewrite, EU expansion.",
    tags: ["wip"], folder: "fo3" },
  { id: "6",  type: "image", category: "image",     name: "Cafe-morning.jpg", size: "3.2 MB", when: "2 weeks ago", thumb: PHOTOS[4], ext: "JPG",
    topic: "Morning light at the cafe, latte and pastry",
    aiContent: "Top-down photo of a wooden cafe table. Latte with rosetta, croissant on a plate, open paperback book. Soft morning sun across the right edge.",
    gps: { lat: 40.72, lng: -73.99, place: "Lower East Side, NY" },
    tags: ["fav"], folder: "fo1" },
  { id: "7",  type: "image", category: "image",     name: "Dog-park-walk.jpg", size: "2.9 MB", when: "2 weeks ago", thumb: PHOTOS[5], ext: "JPG",
    topic: "Off-leash park, golden retriever, golden hour",
    aiContent: "Golden retriever mid-stride on grass field. Two people walking in background. Trees in soft focus. Late afternoon sun.",
    gps: { lat: 37.77, lng: -122.45, place: "Golden Gate Park, SF" },
    tags: [], folder: "fo1" },
  { id: "8",  type: "image", category: "image",     name: "Lake-reflection.jpg", size: "5.6 MB", when: "3 weeks ago", thumb: PHOTOS[6], ext: "JPG",
    topic: "Calm alpine lake, mountain reflection, blue hour",
    aiContent: "Symmetrical mountain reflection in still water. No people. Snow patches on peaks. Pre-dawn blue sky transitioning to peach on horizon.",
    gps: { lat: 46.85, lng: -121.76, place: "Mt. Rainier, WA" },
    tags: ["fav"], folder: "fo1" },
  { id: "9",  type: "image", category: "image",     name: "Wedding-Lea-2.jpg", size: "6.1 MB", when: "1 mo ago", thumb: PHOTOS[7], ext: "JPG",
    topic: "Wedding, candid laughter at reception",
    aiContent: "Bride and groom laughing at reception table. String lights overhead. Warm tungsten light. Léa Bonneau on the right.",
    gps: { lat: 43.71, lng: 7.27, place: "Nice, France" },
    tags: ["fav", "shared"], folder: "fo2" },
  { id: "10", type: "doc",   category: "document",  name: "Lease-agreement-v3.docx", size: "84 KB", when: "1 mo ago", thumb: null, ext: "DOCX",
    topic: "Apartment lease agreement, signed",
    aiContent: "8-page residential lease. Tenant: Alex Rivera. Term: 12 months from June 1. Rent $2,400. Pets allowed. Section 14 covers early termination.",
    tags: ["private"], folder: "fo3" },
  { id: "11", type: "image", category: "image",     name: "City-night.jpg", size: "4.7 MB", when: "1 mo ago", thumb: PHOTOS[8], ext: "JPG",
    topic: "City skyline at blue hour, light trails",
    aiContent: "Long exposure city skyline. Red car taillights below, blue sky above with skyscraper lights. Slight haze.",
    gps: { lat: 35.68, lng: 139.69, place: "Tokyo, Japan" },
    tags: [], folder: "fo2" },
  { id: "12", type: "image", category: "image",     name: "Trail-cabin.jpg", size: "3.4 MB", when: "2 mo ago", thumb: PHOTOS[9], ext: "JPG",
    topic: "Winter cabin in the woods, fresh snowfall",
    aiContent: "Wooden cabin, smoke from chimney, fresh snow on roof and pine trees. Single warm window glow.",
    gps: { lat: 39.19, lng: -106.82, place: "Aspen, CO" },
    tags: [], folder: "fo2" },
  { id: "13", type: "image", category: "image",     name: "Beach-mirror.jpg", size: "2.1 MB", when: "2 mo ago", thumb: PHOTOS[10], ext: "JPG",
    topic: "Tidal flats, mirrored sky reflection",
    aiContent: "Wet beach at low tide acting like a mirror. Pastel sky reflected. Single figure walking far in the distance.",
    gps: { lat: 47.61, lng: -122.34, place: "Olympic Coast, WA" },
    tags: [], folder: "fo1" },
  { id: "14", type: "image", category: "image",     name: "Field-flowers.jpg", size: "5.0 MB", when: "3 mo ago", thumb: PHOTOS[11], ext: "JPG",
    topic: "Wildflower field at dusk, lupines",
    aiContent: "Field of purple lupines. Mountains in distance. Dusk lighting, soft pink to lavender sky.",
    gps: { lat: 36.58, lng: -118.29, place: "Sequoia NP, CA" },
    tags: ["fav"], folder: "fo1" },

  // contacts
  { id: "20", type: "contact", category: "contact", name: "Mom (Maria Rivera).vcf", size: "3 KB", when: "today", thumb: null, ext: "VCF",
    topic: "Personal contact — phone, email, birthday",
    aiContent: "Maria Rivera. Mobile +1 (555) 0140-9921. maria@example.com. Birthday Mar 14. Lives in Portland.",
    tags: ["fav"], folder: "fo4" },
  { id: "21", type: "contact", category: "contact", name: "Dr-Patel-clinic.vcf", size: "2 KB", when: "1 week ago", thumb: null, ext: "VCF",
    topic: "Doctor — primary care",
    aiContent: "Dr. R. Patel. Clinic +1 (555) 0140-3300. office@clinic.example. Address 1820 Main St, Suite 4.",
    tags: [], folder: "fo4" },
  { id: "22", type: "contact", category: "contact", name: "Work-team-export.vcf", size: "18 KB", when: "2 weeks ago", thumb: null, ext: "VCF",
    topic: "Bulk vCard — 12 work contacts",
    aiContent: "12 vCards exported from work directory. Includes: M. Reid, S. Chen, T. Vega, L. Bonneau and 8 others.",
    tags: ["shared"], folder: "fo4" },

  // passwords
  { id: "30", type: "password", category: "password", name: "Bank-vault.kdbx", size: "1.2 MB", when: "yesterday", thumb: null, ext: "KDBX",
    topic: "KeePass vault — 38 entries",
    aiContent: "Encrypted KeePass database. 38 entries across categories: banking, government, work, personal. Last edited yesterday.",
    tags: ["private"], folder: "fo5" },
  { id: "31", type: "password", category: "password", name: "Old-logins-backup.csv", size: "12 KB", when: "1 mo ago", thumb: null, ext: "CSV",
    topic: "CSV password backup — 142 entries",
    aiContent: "142 rows exported from 1Password. Columns: title, username, password (encrypted), url, notes.",
    tags: ["archived"], folder: "fo5" },

  // game saves
  { id: "40", type: "gamesave", category: "gamesave", name: "Stardew-2026-spring-y3.sav", size: "2.8 MB", when: "yesterday", thumb: null, ext: "SAV",
    topic: "Stardew Valley — Year 3, Spring 14",
    aiContent: "Farm: Sweetbriar. Year 3 Spring 14. Net worth 1.2M g. Married Leah. Greenhouse complete. Skull Cavern floor 142.",
    tags: ["fav"], folder: "fo6" },
  { id: "41", type: "gamesave", category: "gamesave", name: "BG3-tav-act3-end.sav", size: "84 MB", when: "3 days ago", thumb: null, ext: "SAV",
    topic: "Baldur's Gate 3 — Act 3, end-game",
    aiContent: "Tav, Sorcerer 11/Warlock 1. Party: Karlach, Shadowheart, Astarion. Act 3, post House of Hope. 88 hours playtime.",
    tags: [], folder: "fo6" },
  { id: "42", type: "gamesave", category: "gamesave", name: "Minecraft-survival-world.zip", size: "212 MB", when: "1 week ago", thumb: null, ext: "ZIP",
    topic: "Minecraft survival world backup",
    aiContent: "1.20.4 world. Spawn village expanded into multi-tier base. Ender Dragon defeated. ~32 chunks explored.",
    tags: ["shared"], folder: "fo6" },

  // IoT data
  { id: "50", type: "iot", category: "iot", name: "Thermostat-2026-04.json", size: "240 KB", when: "today", thumb: null, ext: "JSON",
    topic: "Nest thermostat — April 2026 telemetry",
    aiContent: "30 days of 5-minute samples. Avg indoor temp 21.4°C. Heating cycles: 142. Notable: away mode triggered Apr 8-12.",
    tags: [], folder: "fo7" },
  { id: "51", type: "iot", category: "iot", name: "Solar-inverter-week.csv", size: "48 KB", when: "2 days ago", thumb: null, ext: "CSV",
    topic: "Enphase solar — weekly production",
    aiContent: "Weekly production 162 kWh. Peak Tuesday 14:00 (4.2 kW). Battery cycled 6 times. No fault codes.",
    tags: [], folder: "fo7" },
  { id: "52", type: "iot", category: "iot", name: "Doorlock-events.log", size: "8 KB", when: "today", thumb: null, ext: "LOG",
    topic: "August smart-lock — entry events",
    aiContent: "Last 7 days: 23 unlock events. Codes used: Owner (18), Cleaner (3), Guest-Léa (2). One failed PIN attempt Apr 30 23:41.",
    tags: ["private"], folder: "fo7" },
];

export const FOLDERS = [
  { id: "fo1", name: "Coast 2026",      count: 84,  when: "this week",   types: ["image", "video"] },
  { id: "fo2", name: "Family archive",  count: 412, when: "ongoing",     types: ["image", "video", "document"] },
  { id: "fo3", name: "Receipts & docs", count: 22,  when: "monthly",     types: ["document"] },
  { id: "fo4", name: "Contacts",        count: 14,  when: "ongoing",     types: ["contact"] },
  { id: "fo5", name: "Vaults",          count: 2,   when: "yesterday",   types: ["password"] },
  { id: "fo6", name: "Game saves",      count: 9,   when: "this week",   types: ["gamesave"] },
  { id: "fo7", name: "Home telemetry",  count: 31,  when: "today",       types: ["iot"] },
];

// Recent searches (for clearable history pill UI)
export const RECENT_SEARCHES = [
  "sunset cliffs",
  "Léa wedding",
  "Q1 plan",
  "lease",
  "thermostat",
];

// Aggregate barrel kept for components that pulled `window.NeuthekData.*`
// during the prototype phase. Prefer named imports for new code.
export const NeuthekData = { PHOTOS, FACES, FILES, FOLDERS, TAGS, RECENT_SEARCHES };
