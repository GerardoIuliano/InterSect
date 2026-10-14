(set-info :smt-lib-version 2.6)
(set-logic QF_S)
(set-info :license "https://creativecommons.org/licenses/by/4.0/")
(set-info :category "industrial")
(set-info :status sat)

(declare-const X String)
(assert (not (str.in_re X (re.++ (re.union (str.to_re "h") (str.to_re "t") (str.to_re "p") (str.to_re ":") (str.to_re "/") (str.to_re "w") (str.to_re ".") (str.to_re "|")) (re.+ (re.comp (re.union (str.to_re " ") (str.to_re "\u{9}") (str.to_re "\u{a}") (str.to_re "\u{c}") (str.to_re "\u{d}")))) (str.to_re "\u{a}")))))
(assert (not (str.in_re X (str.to_re "User-Agent:Host:hasHost:ComputerKeylogger.com\u{a}"))))
(assert (not (str.in_re X (re.++ (str.to_re "/filename=") (re.* (re.comp (str.to_re "\u{a}"))) (str.to_re ".zip/i\u{a}")))))
(check-sat)

(exit)
