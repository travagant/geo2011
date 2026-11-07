// Dungeon level 4

id	"dungeon_cave";

scene	"Resources\\Hexagons\\Models\\Arena\\arena_back_dungeon_dragons\\arena_back_dungeon_dragons_hexmap.scene";

// party
// unit "id" lvlcap;
// or
// class amount "unit_side" level lvlcap;

party						
{						
	melee	2	"waterbeasts_giant"	33-45	45	;
}						
						
party						
{						
	melee	2	"waterbeasts_giant"	33-36	36	;
	caster	2	"waterbeasts"	17-28	28	;
}						
						
party						
{						
	melee		"waterbeasts_giant"	33-44	44	;
	caster	2	"waterbeasts"	17-29	29	;
}						
						
party						
{						
	melee		"waterbeasts_giant"	33-36	36	;
	caster	2	"waterbeasts"	17-26	26	;
	caster		"waterbeasts"	17-29	29	;
}						
						
party						
{						
	melee		"waterbeasts_giant"	33-36	36	;
	melee	2	"waterbeasts"	17-26	26	;
	caster		"waterbeasts"	17-29	29	;
}						
						
party						
{						
	melee		"waterbeasts_giant"	33-36	36	;
	melee	2	"waterbeasts"	17-26	26	;
	healer		"waterbeasts"	17-29	29	;
}						
						
party						
{						
	melee		"waterbeasts_giant"	33-36	36	;
	caster	2	"waterbeasts"	17-26	26	;
	healer		"waterbeasts"	17-29	29	;
}						

rewards "dungeon_lvl_4";
