import { Routes, Route, useLocation } from "react-router-dom";
import { useEffect } from "react";
import Banner from "./components/Banner";
import Nav from "./components/Nav";
import Footer from "./components/Footer";
import AutoReveal from "./components/AutoReveal";
import Home from "./pages/Home";
import Features from "./pages/Features";
import Hosting from "./pages/Hosting";
import Developers from "./pages/Developers";
import Roadmap from "./pages/Roadmap";
import Compare from "./pages/Compare";
import Faq from "./pages/Faq";
import Updates from "./pages/Updates";
import UpdateDetail from "./pages/UpdateDetail";
import Waitlist from "./pages/Waitlist";
import VerifyEmail from "./pages/VerifyEmail";
import Unsubscribe from "./pages/Unsubscribe";
import Privacy from "./pages/Privacy";
import Terms from "./pages/Terms";
import Cookies from "./pages/Cookies";
import Dmca from "./pages/Dmca";
import AcceptableUse from "./pages/AcceptableUse";
import Subprocessors from "./pages/Subprocessors";
import Accessibility from "./pages/Accessibility";
import LegalNotice from "./pages/LegalNotice";
import Admin from "./pages/Admin";
import NotFound from "./pages/NotFound";

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" as ScrollBehavior });
  }, [pathname]);
  return null;
}

export default function App() {
  return (
    <>
      <Banner />
      <Nav />
      <ScrollToTop />
      <AutoReveal />
      <main>
        <Routes>
          <Route path="/"           element={<Home />} />
          <Route path="/features"   element={<Features />} />
          <Route path="/hosting"    element={<Hosting />} />
          <Route path="/developers" element={<Developers />} />
          <Route path="/roadmap"    element={<Roadmap />} />
          <Route path="/compare"    element={<Compare />} />
          <Route path="/faq"        element={<Faq />} />
          <Route path="/updates"    element={<Updates />} />
          <Route path="/updates/:slug" element={<UpdateDetail />} />
          <Route path="/waitlist"   element={<Waitlist />} />
          <Route path="/waitlist/verify" element={<VerifyEmail />} />
          <Route path="/unsubscribe" element={<Unsubscribe />} />
          <Route path="/privacy"    element={<Privacy />} />
          <Route path="/terms"      element={<Terms />} />
          <Route path="/cookies"    element={<Cookies />} />
          <Route path="/dmca"       element={<Dmca />} />
          <Route path="/aup"        element={<AcceptableUse />} />
          <Route path="/subprocessors" element={<Subprocessors />} />
          <Route path="/accessibility" element={<Accessibility />} />
          <Route path="/legal-notice"  element={<LegalNotice />} />
          <Route path="/admin"      element={<Admin />} />
          <Route path="*"           element={<NotFound />} />
        </Routes>
      </main>
      <Footer />
    </>
  );
}
