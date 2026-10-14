(set-info :smt-lib-version 2.6)
(set-logic QF_S)
(set-info :license "https://creativecommons.org/licenses/by/4.0/")
(set-info :category "industrial")
(set-info :status sat)

(declare-const X String)
(assert (not (str.in_re X (re.++ (str.to_re "//soft") (re.union (str.to_re "64") (str.to_re "32")) (str.to_re ".dll/U\u{a}")))))
(check-sat)

(exit)
