/** Minimal English/Hindi labels for the student portal. Keys are the English strings; a missing translation falls back to English. */
const HI: Record<string, string> = {
  "My enrolments": "मेरे नामांकन", "Credentials": "प्रमाणपत्र", "Consents": "सहमतियाँ", "Access log": "पहुँच का रिकॉर्ड", "Corrections": "सुधार", "Data requests": "डेटा अनुरोध",
  "My records": "मेरे रिकॉर्ड", "My wallet": "मेरा वॉलेट", "Sign out": "साइन आउट", "Share links": "साझा लिंक", "Withdraw": "वापस लें", "Grant": "अनुमति दें", "Refuse": "अस्वीकार करें",
  "Create share link": "साझा लिंक बनाएँ", "Download certificate": "प्रमाणपत्र डाउनलोड करें", "Revoke": "रद्द करें", "Pending requests": "लंबित अनुरोध", "Active consents": "सक्रिय सहमतियाँ",
  "Who asked about you": "आपके बारे में किसने पूछा", "Request a correction": "सुधार का अनुरोध करें", "Request": "अनुरोध", "Submit": "जमा करें", "Cancel": "रद्द करें",
  "Your enrolment is recorded under statutory authority whether or not you consent. Consent only governs sharing with verifier organisations.": "आपका नामांकन सहमति के बिना भी वैधानिक अधिकार के अंतर्गत दर्ज होता है। सहमति केवल सत्यापन करने वाली संस्थाओं के साथ साझा करने को नियंत्रित करती है।",
  "Refusing APAAR consent never affects whether you are counted.": "APAAR सहमति से इनकार करने पर भी आपकी गिनती प्रभावित नहीं होती।",
  "Your admission is only VERIFIED when your institution, the registry and you all agree.": "आपका प्रवेश तभी सत्यापित माना जाता है जब आपकी संस्था, रजिस्ट्री और आप तीनों सहमत हों।",
  "Valid": "मान्य", "Revoked": "रद्द", "Language": "भाषा",
};
export const tr = (locale: string | undefined, s: string): string => (locale === "hi" ? HI[s] ?? s : s);
