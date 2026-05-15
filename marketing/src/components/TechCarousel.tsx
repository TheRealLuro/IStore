/* Tech carousel — every entry below is a real dependency in pyproject.toml,
   docker-compose.yml, or frontend/package.json. We list names rather than
   trademarked logo bitmaps so we don't misrepresent any vendor's brand. */

const TECH = [
  "FastAPI",
  "PostgreSQL 16",
  "pgvector",
  "MinIO",
  "Redis",
  "Alembic",
  "OpenCLIP (ViT-L-14)",
  "Pillow",
  "Docker Compose",
  "Vite",
  "React 18",
  "TypeScript",
  "PyTorch",
  "argon2-cffi",
  "fastapi-users",
];

export default function TechCarousel() {
  const items = [...TECH, ...TECH]; // duplicate for seamless loop
  return (
    <div className="carousel" aria-label="Technologies neuthek is built on">
      <div className="carousel__track">
        {items.map((label, i) => (
          <span className="carousel__item" key={`${label}-${i}`}>
            <span className="carousel__dot" aria-hidden />
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}
