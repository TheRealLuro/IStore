import { Link } from "react-router-dom";

export default function Banner() {
  return (
    <div className="banner" role="status">
      <span className="banner__dot" aria-hidden />
      <strong>neuthek is in active development.</strong>{" "}
      Nothing is released yet — neither the hosted service nor the
      open-source build. Want the launch ping?
      <Link to="/waitlist">Join the waitlist</Link>.
    </div>
  );
}
