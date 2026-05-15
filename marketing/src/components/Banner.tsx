import { Link } from "react-router-dom";

export default function Banner() {
  return (
    <div className="banner" role="status">
      <span className="banner__dot" aria-hidden />
      <strong>Public hosted version is not live yet.</strong>{" "}
      Self-host today, or
      <Link to="/waitlist">join the waitlist</Link>.
    </div>
  );
}
