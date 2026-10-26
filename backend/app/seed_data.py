"""Static synthetic vocabulary for the seed generator. Everything here is fictional (PRD AS-09)."""

# state -> (region, districts)
GEOGRAPHY = {
    "Tamil Nadu": ("SOUTH", ["Coimbatore", "Chennai", "Madurai", "Tiruchirappalli"]),
    "Karnataka": ("SOUTH", ["Bengaluru Urban", "Mysuru", "Belagavi", "Mangaluru"]),
    "Kerala": ("SOUTH", ["Thiruvananthapuram", "Kochi", "Kozhikode"]),
    "Telangana": ("SOUTH", ["Hyderabad", "Warangal", "Karimnagar"]),
    "Andhra Pradesh": ("SOUTH", ["Visakhapatnam", "Vijayawada", "Guntur"]),
    "Maharashtra": ("WEST", ["Pune", "Nagpur", "Nashik", "Aurangabad"]),
    "Gujarat": ("WEST", ["Ahmedabad", "Surat", "Vadodara", "Rajkot"]),
    "Uttar Pradesh": ("NORTH", ["Lucknow", "Kanpur", "Hardoi", "Varanasi"]),
    "Rajasthan": ("NORTH", ["Jaipur", "Jodhpur", "Udaipur", "Kota"]),
    "Punjab": ("NORTH", ["Ludhiana", "Amritsar", "Patiala"]),
    "Madhya Pradesh": ("CENTRAL", ["Bhopal", "Indore", "Gwalior", "Jabalpur"]),
    "West Bengal": ("EAST", ["Kolkata", "Howrah", "Durgapur", "Siliguri"]),
    "Odisha": ("EAST", ["Bhubaneswar", "Cuttack", "Rourkela"]),
    "Assam": ("NORTH_EAST", ["Guwahati", "Dibrugarh", "Silchar"]),
}

# Fixed demo institutions (PRD 11.3). slug -> (name, type, state, district, aicte_id)
FEATURED = {
    "kit": ("Kaveri Institute of Technology", "ENGINEERING", "Tamil Nadu", "Coimbatore", "1-100001"),
    "bvec": ("Bharatiya Vidya Engineering College", "ENGINEERING", "Maharashtra", "Pune", "1-100002"),
    "mpb": ("Mahanadi Polytechnic", "POLYTECHNIC", "Odisha", "Bhubaneswar", "1-100003"),
    "gce": ("Gangotri College of Engineering", "ENGINEERING", "Uttar Pradesh", "Hardoi", "1-100004"),
    "asm": ("Aravalli School of Management", "MANAGEMENT", "Rajasthan", "Jaipur", "1-100005"),
}

WATER_NAMES = ["Godavari", "Narmada", "Sahyadri", "Vindhya", "Krishna", "Yamuna", "Satluj", "Beas", "Sabarmati", "Tapi", "Periyar", "Cauvery",
               "Tungabhadra", "Brahmaputra", "Teesta", "Damodar", "Ganga", "Ravi", "Chambal", "Betwa", "Indravati", "Hirakud", "Nilgiri", "Himadri",
               "Malwa", "Konkan", "Deccan", "Sundarbans", "Kanchenjunga", "Dhauladhar", "Pir Panjal", "Satpura", "Western Ghats", "Eastern Ghats",
               "Vaigai", "Pennar", "Mahi", "Luni", "Ghaghara", "Gomti", "Son", "Kosi", "Subarnarekha", "Brahmani", "Baitarani", "Sharavati", "Netravati",
               "Bhima", "Pranhita", "Manjira", "Musi", "Tambraparni", "Meenachil", "Pamba", "Chaliyar", "Barak", "Subansiri", "Dihing"]

ENGINEERING_PROGRAMMES = [("B.E. Computer Science and Engineering", "CSE", "UG", 4), ("B.E. Electronics and Communication", "ECE", "UG", 4),
                          ("B.E. Mechanical Engineering", "MECH", "UG", 4), ("B.E. Civil Engineering", "CIVIL", "UG", 4),
                          ("B.E. Electrical and Electronics", "EEE", "UG", 4), ("B.Tech Information Technology", "IT", "UG", 4),
                          ("B.Tech Artificial Intelligence and Data Science", "AIDS", "UG", 4), ("M.Tech Computer Science", "MTCS", "PG", 2),
                          ("M.Tech VLSI Design", "MTVLSI", "PG", 2)]
POLY_PROGRAMMES = [("Diploma in Mechanical Engineering", "DME", "DIPLOMA", 3), ("Diploma in Civil Engineering", "DCE", "DIPLOMA", 3),
                   ("Diploma in Electrical Engineering", "DEE", "DIPLOMA", 3), ("Diploma in Computer Engineering", "DCO", "DIPLOMA", 3)]
MGMT_PROGRAMMES = [("PGDM General Management", "PGDM", "PGDM", 2), ("PGDM Executive (Part Time)", "PGDMX", "PGDM", 2), ("MBA", "MBA", "PG", 2)]
PHARMA_PROGRAMMES = [("B.Pharm", "BPH", "UG", 4), ("D.Pharm", "DPH", "DIPLOMA", 2), ("M.Pharm", "MPH", "PG", 2)]
UNI_PROGRAMMES = [("M.Tech Data Science", "MTDS", "PG", 2), ("MBA", "MBA", "PG", 2), ("B.Tech Computer Science", "BTCS", "UG", 4),
                  ("B.Arch Architecture", "BARCH", "UG", 5)]

FIRST_M = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Ayaan", "Krishna", "Ishaan", "Rohan", "Kabir", "Rahul", "Karan", "Nikhil",
           "Siddharth", "Pranav", "Harsh", "Manish", "Deepak", "Suresh", "Ramesh", "Vikram", "Amit", "Rajesh", "Anil", "Sanjay", "Mohammed", "Imran",
           "Farhan", "Tarun", "Varun", "Gautam", "Yash", "Dev", "Naveen", "Prakash", "Ganesh", "Mahesh", "Ashok", "Bhaskar", "Chandan", "Dinesh"]
FIRST_F = ["Aadhya", "Diya", "Ananya", "Saanvi", "Priya", "Isha", "Meera", "Kavya", "Riya", "Neha", "Pooja", "Sneha", "Anjali", "Divya", "Lakshmi",
           "Shruti", "Fatima", "Ayesha", "Zara", "Nandini", "Swati", "Tanvi", "Aditi", "Bhavna", "Charu", "Deepika", "Gayatri", "Harini", "Jyoti",
           "Kiran", "Lata", "Madhuri", "Nisha", "Padma", "Radhika", "Sunita", "Usha", "Vandana"]
SURNAMES = {
    "SOUTH": ["Iyer", "Nair", "Reddy", "Naidu", "Menon", "Pillai", "Rao", "Gowda", "Krishnan", "Subramanian", "Varma", "Shetty", "Kumar"],
    "WEST": ["Patil", "Deshmukh", "Kulkarni", "Shah", "Patel", "Joshi", "Desai", "Mehta", "Jadhav", "Pawar", "More", "Kale"],
    "NORTH": ["Sharma", "Verma", "Singh", "Gupta", "Yadav", "Mishra", "Tiwari", "Chauhan", "Rathore", "Kapoor", "Khan", "Bansal"],
    "CENTRAL": ["Dubey", "Pandey", "Shukla", "Tripathi", "Sahu", "Thakur", "Jain", "Agrawal", "Soni", "Rajput"],
    "EAST": ["Das", "Banerjee", "Chatterjee", "Mukherjee", "Sen", "Ghosh", "Mohanty", "Behera", "Panda", "Nayak", "Sahoo", "Mishra"],
    "NORTH_EAST": ["Bora", "Gogoi", "Hazarika", "Baruah", "Sarma", "Das", "Choudhury", "Saikia", "Kalita", "Dutta"],
}
EMAIL_DOMAIN = "example.test"
FICTIONAL_DEMO_STUDENTS = {  # username -> (first, surname, gender)
    "st.aarav": ("Aarav", "Iyer", "M"), "st.diya": ("Diya", "Nair", "F"), "st.priya": ("Priya", "Krishnan", "F"), "st.rohan": ("Rohan", "Mohanty", "M"),
    "st.ishaan": ("Ishaan", "Behera", "M"), "st.fatima": ("Fatima", "Khan", "F"), "st.vikram": ("Vikram", "Patil", "M"),
}
