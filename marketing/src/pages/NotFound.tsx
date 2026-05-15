import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <section className="section section--loose">
      <div className="container fade-in" style={{ textAlign: "center" }}>
        <span className="eyebrow">404</span>
        <h1>That page doesn't exist.</h1>
        <p className="lead" style={{ margin: "16px auto 32px" }}>
          The page you tried to reach isn't here. Try the home page or
          the roadmap.
        </p>
        <Link to="/" className="btn btn--primary btn--lg">Back home</Link>
      </div>
    </section>
  );
}
